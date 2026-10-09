"""70-100 秒产品演示：python3 scripts/record_demo.py
新版界面：五分组数字卡、条件卡试算药丸、LLM 长尾接地、否定挂起、合规拦截。"""
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
    W(pg, 2000)

    # 标准三连 → 流式解读 → 条件卡 + 精确试算
    pg.fill("#q", "经营改善、估值合理、走势相对稳定，剔除 ST 和上市不满一年的新股")
    W(pg, 1000); pg.click("#go")
    pg.wait_for_selector("#condCard", timeout=20000); W(pg, 6000)
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 3000)

    # 运行 → 数字概览 → 证据展开 → 分组切换
    pg.click("#run"); pg.wait_for_selector(".stock", timeout=15000); W(pg, 2500)
    pg.locator(".stock .h").first.click(); W(pg, 4500)
    pg.click('.stat[data-tab="near_miss"]'); W(pg, 2500)
    pg.click('.stat[data-tab="unknown_data"]'); W(pg, 2500)
    pg.click('.stat[data-tab="selected"]'); W(pg, 800)

    # 改严改松 → diff 高亮
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 600)
    vol_card(pg).locator(".tier", has_text="严格").click(); W(pg, 1500)
    pg.click("#run"); W(pg, 2500)
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 600)
    vol_card(pg).locator(".tier", has_text="宽松").click(); W(pg, 1500)
    pg.click("#run")
    pg.wait_for_function("document.querySelector('#diffInfo')?.textContent.includes('新增')", timeout=20000)
    W(pg, 3500)

    # 行业集合条件
    pg.fill("#q", "只看银行板块，估值合理"); pg.click("#go")
    pg.wait_for_selector("#condCard", timeout=20000); W(pg, 4500)
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 1000)
    pg.click("#run"); pg.wait_for_selector(".stock"); W(pg, 3000)
    pg.locator(".stock .h").first.click(); W(pg, 3500)

    # LLM 长尾接地（规则未覆盖 → AI 补条件，过门禁可编辑）
    pg.fill("#q", "能持续赚钱、负债不多的公司"); pg.click("#go")
    pg.wait_for_selector(".src.llm", timeout=20000); W(pg, 6000)
    pg.eval_on_selector("#condCard", "el => el.scrollIntoView()"); W(pg, 2500)

    # 否定处理（反直觉否定挂起，不硬翻）
    pg.fill("#q", "不便宜的好公司"); pg.click("#go"); W(pg, 5000)

    # 合规拦截
    pg.fill("#q", "推荐几个下周涨停的黑马"); pg.click("#go"); W(pg, 5000)

    ctx.close(); b.close()
print("done:", OUT)
