import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings


@dataclass(slots=True)
class StagedUpload:
    temporary_path: Path
    sha256: str
    size_bytes: int
    storage_key: str

    def commit(self) -> Path:
        destination = settings.upload_dir / self.storage_key
        self.temporary_path.replace(destination)
        return destination

    def discard(self) -> None:
        self.temporary_path.unlink(missing_ok=True)


class LocalDocumentStorage:
    chunk_size = 1024 * 1024

    async def stage_pdf(self, upload: UploadFile) -> StagedUpload:
        filename = upload.filename or "document.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="当前仅支持 PDF 文件。",
            )

        settings.ensure_directories()
        storage_key = f"{uuid4()}.pdf"
        temporary_path = settings.upload_dir / f".{storage_key}.part"
        digest = hashlib.sha256()
        size_bytes = 0
        first_chunk = True

        try:
            with temporary_path.open("wb") as target:
                while chunk := await upload.read(self.chunk_size):
                    if first_chunk:
                        first_chunk = False
                        if not chunk.lstrip().startswith(b"%PDF-"):
                            raise HTTPException(
                                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                                detail="文件扩展名为 PDF，但内容不是有效的 PDF。",
                            )
                    size_bytes += len(chunk)
                    if size_bytes > settings.max_upload_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"PDF 超过 {settings.max_upload_mb} MB 限制。",
                        )
                    digest.update(chunk)
                    target.write(chunk)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        if size_bytes == 0:
            temporary_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="上传的 PDF 为空。")

        return StagedUpload(
            temporary_path=temporary_path,
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
            storage_key=storage_key,
        )

    def document_path(self, storage_key: str) -> Path:
        path = (settings.upload_dir / storage_key).resolve()
        if path.parent != settings.upload_dir.resolve():
            raise ValueError("Invalid storage key")
        return path

    def parsed_path(self, document_id: str) -> Path:
        return settings.parsed_dir / f"{document_id}.json"

    def delete(self, storage_key: str, document_id: str) -> None:
        self.document_path(storage_key).unlink(missing_ok=True)
        self.parsed_path(document_id).unlink(missing_ok=True)


storage = LocalDocumentStorage()

