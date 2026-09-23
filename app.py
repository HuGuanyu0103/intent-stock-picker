"""自然语言智能选股与策略解释器 —— Flask 单服务。
LLM（规则解析/可选大模型）只产出条件 JSON 与解读文案；判定全部由 core.engine 完成。"""
import json
import os
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from core import metrics, parser as intent_parser, engine

app = Flask(__name__, static_folder="static", static_url_path="")

MATRIX_PATH = os.path.join(os.path.dirname(__file__), "data", "matrix.json")
if os.path.exists(MATRIX_PATH):
    with open(MATRIX_PATH, encoding="utf-8") as f:
        MATRIX = json.load(f)
else:
    MATRIX = metrics.build_matrix()


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/health")
def health():
    return jsonify({"as_of": MATRIX["as_of"], "n": MATRIX["n"],
                    "mode": "sample" if not os.environ.get("FUYAO_API_KEY") else "live",
                    "engine": "确定性三值判定，LLM 不参与选股结果"})


@app.post("/api/parse")
def parse_stream():
    """SSE：逐句推送解读流，最后一个事件为结构化条件。"""
    text = (request.json or {}).get("text", "")
    result = intent_parser.parse(text)

    def gen():
        for i, sentence in enumerate(result.get("narrative", [])):
            time.sleep(0.55)  # 渐进式输出，营造可读节奏
            yield f"event: chunk\ndata: {json.dumps({'i': i, 'text': sentence}, ensure_ascii=False)}\n\n"
        yield f"event: done\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/trials")
def trials():
    conditions = (request.json or {}).get("conditions", [])
    return jsonify({"trials": engine.trial_counts(MATRIX, conditions),
                    "conflicts": engine._hard_conflicts(conditions)})


@app.post("/api/run")
def run():
    conditions = (request.json or {}).get("conditions", [])
    result = engine.run(MATRIX, conditions)
    result["as_of"] = MATRIX["as_of"]
    result["pool_n"] = MATRIX["n"]
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
