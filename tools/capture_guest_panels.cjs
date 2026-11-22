// Run with Playwright against the matching public demo after rebuilding its dashboards.
const { chromium } = require("playwright");
const fs = require("node:fs/promises");
const path = require("node:path");
const base = new URL(process.argv[2] || "http://localhost:8123");
const root = path.resolve(__dirname, "..");

async function capture() {
  const out = path.join(root, "weather/static/panel-previews");
  await fs.mkdir(out, {recursive:true});
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || undefined});
  try {
    const page = await browser.newPage({locale:"ru-RU", timezoneId:"UTC"});
    for (const code of ["kr2","kr1","ame60"]) {
      const file = path.join(root, "ops/guest-grafana/dashboards", code+".json");
      const board = JSON.parse(await fs.readFile(file,"utf8"));
      const endpoint = new URL("/charts/demo/api/dashboards/uid/"+board.uid, base);
      const response = await page.request.get(endpoint.href);
      if (!response.ok()) throw Error("Dashboard unavailable: "+board.uid);
      const live = (await response.json()).dashboard;
      const assert = require("node:assert/strict");
      for (const panel of board.panels) {
        const installed = live.panels.find(p=>p.id===panel.id);
        assert.deepEqual(installed.targets[0].snapshot, panel.targets[0].snapshot);
        const url = new URL("/charts/demo/d-solo/"+board.uid+"/archive", base);
        url.search = new URLSearchParams({panelId:panel.id,theme:"light",timezone:"utc"});
        for (const [size,width,height] of [["wide",1080,300],["narrow",380,245]]) {
          await page.setViewportSize({width,height});
          await page.goto(url.href,{waitUntil:"domcontentloaded"});
          await page.locator(".uplot canvas").first().waitFor();
          await page.evaluate(async()=>{await document.fonts.ready; await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));});
          await page.screenshot({path:path.join(out,code+"-"+panel.id+"-"+size+".png")});
        }
      }
    }
  } finally { await browser.close(); }
}
capture().catch(error=>{console.error(error);process.exitCode=1;});
