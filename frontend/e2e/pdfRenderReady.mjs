/** Wait for actual page pixels, not only a mounted canvas or a coordinate overlay. */
export async function waitForPaintedPdfPage(dialog, pageNumber) {
  const canvas = dialog.locator(`.react-pdf__Page[data-page-number="${pageNumber}"] canvas`);
  await canvas.waitFor({ state: "visible", timeout: 45_000 });
  await canvas.evaluate(async (element) => {
    const deadline = Date.now() + 45_000;
    while (Date.now() < deadline) {
      const context = element.getContext("2d");
      if (element.width > 100 && element.height > 100 && context) {
        const pixels = context.getImageData(0, 0, element.width, element.height).data;
        let painted = 0;
        for (let offset = 0; offset < pixels.length; offset += 16) {
          if (pixels[offset + 3] > 0 && Math.min(pixels[offset], pixels[offset + 1], pixels[offset + 2]) < 200) {
            if (++painted >= 100) return;
          }
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("PDF canvas stayed blank after 45 seconds");
  });
  await dialog.locator(`.react-pdf__Page[data-page-number="${pageNumber}"] .react-pdf__Page__textContent span`).first().waitFor();
}
