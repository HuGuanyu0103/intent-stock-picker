"""录制 60-180 秒产品演示（playwright webm）：python3 scripts/record_demo.py"""
import os
from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "demo_video")
os.makedirs(OUT, exist_ok=True)
W = lambda pg, ms: pg.wait_for_timeout(ms)


def vol_card(pg):
    return pg.locator(".cond", has_text="波动率分位")


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1366, "height": 820}, record_video_dir=OUT,
                        record_video_size={"width": 1366, "height": 820})
    pg = ctx.new_page()
    pg.goto("http://127.0.0.1:8080", wait_until="networkidle")
    W(pg, 2500)

    pg.fill("#q", "经营改善、估值合理、走势相对稳定，剔除 ST 和上市不满一年的新股")
    W(pg, 1200)
    pg.click("#go")
    pg.wait_for_selector("#condCard", timeout=20000)
    W(pg, 7000)
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()")
    W(pg, 2500)

    pg.click("#run")
    pg.wait_for_selector(".stock", timeout=15000)
    pg.eval_on_selector("#resCard", "el => el.scrollIntoView()")
    W(pg, 2000)
    pg.locator(".stock .h").first.click()
    W(pg, 5500)
    pg.click("text=差一点")
    W(pg, 3000)
    pg.click("text=数据不足")
    W(pg, 3000)
    pg.click("text=入选")
    W(pg, 1000)

    # 改严
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 800)
    vol_card(pg).locator(".tier", has_text="严格").click(); W(pg, 1800)
    pg.click("#run"); W(pg, 3500)

    # 改松（新增标记）
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 800)
    vol_card(pg).locator(".tier", has_text="宽松").click(); W(pg, 1800)
    pg.click("#run")
    pg.wait_for_function("document.querySelector('#diffInfo')?.textContent.includes('新增 7')", timeout=20000)
    pg.eval_on_selector("#resCard", "el => el.scrollIntoView()")
    W(pg, 6000)

    # 回适中 + 追加条件
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 800)
    vol_card(pg).locator(".tier", has_text="适中").click(); W(pg, 1500)
    pg.fill("#refine", "成交活跃")
    pg.click("#refineBtn")
    pg.wait_for_function("document.querySelector('#diffInfo')?.textContent.includes('掉出')", timeout=20000)
    W(pg, 5500)

    # 合规
    pg.fill("#q", "推荐几个下周涨停的黑马")
    pg.click("#go")
    W(pg, 6500)

    ctx.close()
    b.close()
print("done:", OUT)
