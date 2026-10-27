"""Restore audited public ASTs only into the dedicated recording database.

This is fixture restoration, never a claim of fresh parsing. Existing documents
are preserved by content hash (including those parsed by the real Worker).
"""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.document import Document
from app.services.chunking import replace_document_chunks


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    # Fail closed: this helper is not a general database importer.
    if (settings.database_url != 'postgresql+psycopg://recording:recording-local@recording-postgres:5432/recording'
            or settings.vector_collection != 'recording_20261024_public_v1'
            or settings.data_dir.resolve() != Path('/app/data')):
        raise ValueError('Only the isolated recording compose environment is permitted')
    source = Path('/app/data/prepared-graph')
    rows = json.loads((source / 'fixture.json').read_bytes())
    if len(rows) != 20:
        raise ValueError('Require the pinned 20-paper public selection')
    # Validate the whole input before any writes.
    for row in rows:
        for folder, suffix, key in (('uploads', '.pdf', 'source_sha256'), ('parsed', '.json', 'ast_sha256')):
            path = source / folder / (row['document_id'] + suffix)
            if path.parent != source / folder or digest(path) != row[key]:
                raise ValueError('Source identity changed')
    manifest, inserted, preserved = [], [], []
    with sqlite3.connect((source / 'acceptance.db').as_uri() + '?mode=ro', uri=True) as old, SessionLocal() as db:
        old.row_factory = sqlite3.Row
        for row in rows:
            doc = db.scalar(select(Document).where(Document.sha256 == row['source_sha256']))
            if doc is None:
                record = dict(old.execute('SELECT * FROM documents WHERE id = ?', (row['document_id'],)).fetchone())
                if record['status'] != 'ready' or record['sha256'] != row['source_sha256']:
                    raise ValueError('Archived metadata differs')
                fields = ('id', 'original_filename', 'storage_key', 'size_bytes', 'sha256',
                          'status', 'title', 'authors_json', 'page_count', 'arxiv_id',
                          'arxiv_version', 'parser_name', 'parser_version', 'semantic_signature')
                doc = Document(**{key: record[key] for key in fields})
                pdf = settings.upload_dir / (doc.id + '.pdf')
                ast = settings.parsed_dir / (doc.id + '.json')
                for destination, origin, expected in (
                    (pdf, source / 'uploads' / (doc.id + '.pdf'), row['source_sha256']),
                    (ast, source / 'parsed' / (doc.id + '.json'), row['ast_sha256'])):
                    if destination.exists():
                        # A rolled-back DB transaction may leave copied files.
                        # Reuse only identical owned inputs; never replace them.
                        if digest(destination) != expected:
                            raise ValueError('Refuse mismatching unregistered fixture files')
                    else:
                        shutil.copyfile(origin, destination)
                db.add(doc)
                db.flush()
                replace_document_chunks(db, doc.id, json.loads(ast.read_bytes()))
                inserted.append(row['filename'])
            else:
                if doc.status != 'ready':
                    raise ValueError('Existing recording document is not ready')
                preserved.append(row['filename'])
            manifest.append({'document_id': doc.id, 'filename': row['filename'],
                'source_sha256': row['source_sha256'],
                'ast_sha256': digest(settings.parsed_dir / (doc.id + '.json'))})
        db.commit()
    (settings.data_dir / 'fixture.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    report = {'restored_not_reparsed': inserted, 'existing_worker_documents_preserved': preserved,
              'cloud_calls': 0, 'vectors_written': 0, 'production_library_modified': False}
    (settings.data_dir / 'fixture-restoration.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
