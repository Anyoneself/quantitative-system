const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

async function main() {
  const url = process.argv[2];
  if (!url || !/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) {
    throw new Error("需要本机测试服务地址，例如 http://127.0.0.1:8000");
  }
  const output = process.argv[3] || "/tmp/quant-research-ui";
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(url);
      if (viewport.width < 720) await page.locator("#menu-button").click();
      await page.locator('.nav-item[data-page="experiments"]').click();
      await page.locator("#experiment-submit").click();
      await page.locator("#experiment-result").waitFor({ state: "visible", timeout: 60000 });
      assert.match(await page.locator("#experiment-mode").innerText(), /合成演示.*实盘禁用/);
      assert.equal(await page.locator("#experiment-checks tr").count(), 11);
      assert.ok(await page.locator("#experiment-orders tr").count() > 0);
      assert.equal(await page.locator("#experiment-page-count").innerText(), "1 / 3");
      await page.locator("#experiment-next").click();
      assert.equal(await page.locator("#experiment-page-count").innerText(), "2 / 3");
      await page.locator("#experiment-prev").click();
      await page.evaluate(() => window.scrollTo(0, 0));
      const geometry = await page.evaluate(() => {
        const canvas = document.querySelector("#experiment-chart");
        const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
        let colored = 0;
        for (let index = 0; index < pixels.length; index += 4) {
          if (pixels[index + 3] > 0 && pixels[index + 2] > pixels[index] + 40) colored++;
        }
        return {
          overflow: document.documentElement.scrollWidth > window.innerWidth,
          canvasWidth: canvas.clientWidth, canvasHeight: canvas.clientHeight, colored,
        };
      });
      assert.equal(geometry.overflow, false, "页面不应出现水平溢出");
      assert.ok(geometry.canvasWidth > 250 && geometry.canvasHeight >= 200);
      assert.ok(geometry.colored > 100, "净值曲线不能是空画布");
      await page.screenshot({ path: path.join(output, `research-${viewport.width}.png`), fullPage: true });
      await page.screenshot({ path: path.join(output, `research-${viewport.width}-viewport.png`) });
      const downloadEvent = page.waitForEvent("download");
      await page.locator("#experiment-download").click();
      const download = await downloadEvent;
      assert.match(download.suggestedFilename(), /\.json$/);
      const selected = await page.locator("#experiment-history").inputValue();
      await page.reload();
      if (viewport.width < 720) await page.locator("#menu-button").click();
      await page.locator('.nav-item[data-page="experiments"]').click();
      await page.locator(`#experiment-history option[value="${selected}"]`).waitFor({ state: "attached" });
      await page.locator("#experiment-history").selectOption(selected);
      await page.locator("#experiment-result").waitFor({ state: "visible" });
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ viewport, geometry, reportReloaded: true, errors }));
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
