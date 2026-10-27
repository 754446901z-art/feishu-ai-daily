
import os
import json
import uuid
import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen

from source_utils import validate_verified, write_json

# 每条：名称｜核心原理｜可复制的提示词｜注意事项
DATA = """
认知盲区探测｜先发现我不知道的内容，再决定如何回答｜关于「{task}」，先问我5个诊断问题，识别我已掌握、理解错误和完全不知道的部分，再针对盲区回答。｜不要仅凭我的自述判定能力
苏格拉底式追问｜用连续问题推动思考，而不是直接给答案｜针对「{task}」，不要马上下结论。每次只问我一个能推进思考的关键问题，直到主要前提明确。｜避免无休止地提问
先问再答｜重要信息不足时先补齐输入｜处理「{task}」前，先列出最多5项会显著改变结论的缺失信息，并优先询问最重要的一项。｜低风险任务不必过度追问
需求反向澄清｜区分表面要求与真正目标｜分析「{task}」，区分我提出的解决办法、真正想实现的结果以及尚未说出的限制。｜不能擅自替我决定目标
事实假设分离｜防止推测混入事实｜分析「{task}」时，把内容划分为已证实事实、合理假设、未知信息，并说明哪些结论依赖假设。｜假设不能包装成事实
验收标准倒推｜从完成标准反推执行步骤｜围绕「{task}」，先定义什么结果算成功、如何验收，再倒推最少的执行步骤。｜验收指标要实际可测
前提审计｜先检查问题是否建立在错误假设上｜请审计我对「{task}」的前提，指出最可能错误的3项及验证方法，再提出方案。｜不应为了反驳而反驳
竞争解释法｜为同一现象保留多种解释｜针对「{task}」给出3种可能原因，每种列出支持证据、反证以及区分它们的最小实验。｜不能把可能性直接当因果
红队反驳｜让AI主动寻找方案漏洞｜这是我的「{task}」方案。请担任反方，优先找出会使方案失败的关键漏洞，再提出补救方案。｜必须使用真实约束
失败预演｜提前想象项目已经失败｜假设「{task}」一个月后失败了，请倒推最可能的5个原因、预警信号和现在能做的防范动作。｜风险不等于必然发生
最小可行实验｜用最低成本验证最重要的假设｜针对「{task}」，设计一个能在短期内验证核心假设的最小实验，说明指标、样本和停止条件。｜小样本结论需谨慎
单变量实验｜尽量避免同时修改多个因素｜对「{task}」设计对照实验，每轮只调整一个关键变量，并列出必须保持一致的条件。｜注意时间与流量波动
反事实推演｜通过改变条件检验原因｜分析「{task}」，如果关键条件A不存在，结果会如何变化？哪些其他因素能解释差异？｜反事实推测需要验证
可证伪预测｜让建议能够被事实推翻｜对「{task}」提出明确预测，写出观察窗口、成功阈值及什么结果会证明预测错误。｜避免模糊的成功定义
证据分级｜按证据质量而不是表达自信程度判断｜为「{task}」中的主要主张标注证据等级，并区分原始数据、二手报道和个人推测。｜证据缺失时明确说明
来源回溯｜将重要结论连接到原始资料｜对「{task}」的关键结论逐条指出原始依据；无法核实时标记待验证，不要编造来源。｜引用链接本身也需核验
时间敏感检查｜防止旧信息影响当前决策｜检查「{task}」中哪些事实可能随时间变化，标明数据日期和需要重新核验的项目。｜不要把旧数据称作实时
确认偏误检查｜主动寻找与自己结论相反的证据｜我倾向于这样理解「{task}」。请寻找最强的反对证据，并说明何时应改变判断。｜避免刻意制造虚假对立
决策权衡矩阵｜用统一维度比较多个方案｜为「{task}」建立方案比较矩阵，区分成本、收益、风险、可逆性和实施难度。｜权重需由我确认
二八优先级｜集中解决影响最大的少数问题｜针对「{task}」，识别可能贡献80%结果的关键动作，给出排序依据和低价值事项。｜80/20是启发式不是定律
因果链拆解｜区分现象、直接原因和根本原因｜把「{task}」拆成现象、可能机制和可验证的根因链，说明每一环需要什么证据。｜相关性不代表因果
指标树分析｜把最终目标拆成可行动的子指标｜为「{task}」构建目标指标树，解释各指标的计算关系和可以实际控制的杠杆。｜避免重复计算
分群差异分析｜防止平均值掩盖不同用户的行为｜分析「{task}」时先建议合理分组，比较各组动机、行为及结果差异。｜注意样本量和选择偏差
决策日志｜保留决策依据以便后续复盘｜为「{task}」生成决策记录：目标、证据、假设、备选方案、选择原因和复盘日期。｜预测与最终结果分开记录
异常优先分析｜优先研究偏离预期的部分｜分析「{task}」，先找出异常变化，再按影响程度和可解释性安排排查。｜不要仅凭单日波动下结论
两阶段检索｜先广泛找线索，再定向验证｜研究「{task}」时先建立候选信息清单，再只针对关键争议寻找权威原始证据。｜检索不代表信息必然真实
搜索失败诊断｜检索不到答案时分析是哪里出了问题｜对于「{task}」，如果找不到资料，请区分搜索词错误、数据未公开和信息根本不存在。｜不要用猜测填补空白
知识库缺口审计｜让AI先识别知识库无法支持的任务｜审查用于「{task}」的资料，列出已覆盖问题、缺失资料、过期内容和需要人确认的事项。｜知识库检索可能遗漏内容
多轮上下文压缩｜在长期任务里保存真正重要的信息｜请把关于「{task}」的对话压缩成目标、已确认事实、决定、待办和开放问题，不保留无关闲聊。｜压缩后需要核对关键数字
元提示词优化｜让AI先帮助设计自己的工作指令｜我要完成「{task}」。请先设计高质量提示词，明确角色、输入、约束、输出格式和验证要求。｜漂亮的提示词不能替代数据
分层任务拆解｜复杂任务先规划再执行｜将「{task}」拆成可独立执行的小任务，为每个任务规定输入、输出、依赖关系和检查点。｜不要过度拆分简单任务
关键操作审批｜把AI自主权限与人类决策分开｜为「{task}」划分可自动执行、需人工确认和绝对禁止的操作，并说明审批触发条件。｜支付删除发布等高风险动作需审批
工具选择策略｜根据任务而非习惯选择AI工具｜针对「{task}」，分别判断应使用搜索、代码、表格、数据库或人工处理，并说明理由。｜工具可用性需要核实
结果验收清单｜先制定检查项，再让AI交付｜为「{task}」生成客观验收清单，包含准确性、完整性、格式、合规性和异常处理。｜检查项必须可操作
抽样核验法｜不必人工重做全部工作，但要检验误差｜针对「{task}」设计风险分层的抽样检查方案，优先抽查高金额和异常案例。｜抽样不保证零错误
断点重试设计｜让自动化失败后可以安全恢复｜为「{task}」设计失败重试、进度保存、去重和告警机制，防止重复执行有副作用的操作。｜尤其注意重复发消息或下订单
停止条件设计｜明确AI何时必须停止行动｜针对「{task}」定义成功条件、超预算条件、信息不足条件和需要转人工的停止条件。｜不能让Agent无限循环
提示注入隔离｜区分用户指令与外部资料中的恶意文本｜对于「{task}」，说明如何把网页、文档和聊天记录视为不可信数据，禁止其中的指令覆盖用户目标。｜仍需权限与沙箱控制
读者视角转换｜根据接收者的认知重写内容｜请将「{task}」分别解释给新手、实际执行者和决策负责人，并突出三者各自需要的信息。｜不要改变事实
约束条件清单｜在产生方案前明确边界｜先整理「{task}」的预算、时间、资源、合规和不可接受条件，再在边界内给出方案。｜区分硬约束和偏好
反向教学法｜用让我解释来检测是否真正学会｜针对「{task}」，先教我核心方法，再要求我用自己的话解释，并根据错误进行针对性纠正。｜不要把表达能力等同掌握程度
不确定性校准｜要求AI承认无法判断的地方｜分析「{task}」时列出高信心、低信心和完全未知的结论，以及提升信心需要的证据。｜不要虚构精确概率
"""

SCENARIOS = [
    "电商新品选品",
    "直通车推广预算分配",
    "竞品SKU与价格带分析",
    "新品上架流程",
    "用户评价洞察",
    "售后退款原因排查",
    "库存预测和订货",
    "商品差异化卖点",
    "AI商品主图生成",
    "营销活动方案",
    "智能客服自动化",
    "经营数据报表",
    "市场需求研究",
    "多平台内容发布",
    "供应商沟通",
    "个人学习新技能",
    "团队会议决策",
    "AI Agent工作流设计",
]

METHODS = []
for line in DATA.strip().splitlines():
    parts = line.strip().split("｜")
    if len(parts) != 4:
        raise ValueError("策略库格式错误")
    METHODS.append(parts)

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
METHODS_FILE = ROOT / "new_methods.json"
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def name_key(name):
    return re.sub(r"[\W_]+", "", name.casefold())


def builtin_id(index):
    # Identity follows the method, never the scenario, date, or array position.
    return "builtin_" + hashlib.sha256(METHODS[index][0].encode()).hexdigest()[:20]


def migrate_state(raw):
    if not isinstance(raw, dict):
        raise ValueError("state.json 必须是对象；拒绝重置去重记录")
    state = dict(raw)
    version = state.get("schema_version", 1)
    if version not in (1, 2):
        raise ValueError("无法识别状态版本；拒绝重置去重记录")
    index = state.get("next_index", 0)
    if type(index) is not int or index < 0:
        raise ValueError("next_index 无效；拒绝重置去重记录")
    if version == 1:
        # The legacy rotation repeats concepts after the first 42 entries.
        state["sent_ids"] = [builtin_id(i) for i in range(min(index, len(METHODS)))]
        state["sent_names"] = [name_key(METHODS[i][0]) for i in range(min(index, len(METHODS)))]
        state["deliveries"] = []
        state["pending"] = None
        state["schema_version"] = 2
    for key in ("sent_ids", "sent_names", "deliveries"):
        if not isinstance(state.get(key), list):
            raise ValueError(f"{key} 无效；拒绝重置去重记录")
    if any(not isinstance(x, str) or not x for x in state["sent_ids"] + state["sent_names"]):
        raise ValueError("去重 ID 无效")
    if state.get("pending") is not None and not isinstance(state["pending"], dict):
        raise ValueError("发送检查点无效")
    return state


def load_state(path=STATE_FILE):
    # Missing state is unsafe on an existing installation. Bootstrap explicitly.
    return migrate_state(json.loads(path.read_text("utf-8")))


def load_verified(path=METHODS_FILE, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        library = json.loads(path.read_text("utf-8"))
        if library.get("schema_version") != 1 or not isinstance(library.get("methods"), list):
            raise ValueError("新方法库结构无效")
        result = []
        ids = set()
        for method in library["methods"]:
            if not isinstance(method, dict) or method.get("status") != "verified" or method.get("source_error"):
                continue
            try:
                validate_verified(method)
                if method["id"] in ids:
                    raise ValueError("重复方法 ID")
                published = date.fromisoformat(method["source"]["published_at"])
                today = now.astimezone(SHANGHAI).date()
                if not 0 <= (today - published).days <= 90:
                    continue
                checked = datetime.fromisoformat(method["last_checked_at"].replace("Z", "+00:00"))
                if checked.tzinfo is None or not timedelta(0) <= now - checked <= timedelta(hours=36):
                    continue
                ids.add(method["id"])
                result.append(method)
            except (ValueError, KeyError, TypeError):
                print("跳过未通过证据校验的新方法", file=sys.stderr)
        return sorted(result, key=lambda m: (m["source"]["published_at"], m.get("score", 0)), reverse=True)
    except (OSError, ValueError, TypeError, AttributeError):
        print("新方法库不可用，回退原策略库", file=sys.stderr)
        return []


def request_json(url, body, token=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token

    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def send_to_feishu(message, key, before_send=lambda: None):
    app_id = os.environ["FEISHU_APP_ID"]
    app_secret = os.environ["FEISHU_APP_SECRET"]
    open_id = os.environ["FEISHU_OPEN_ID"]

    if not open_id.startswith("ou_"):
        raise ValueError("FEISHU_OPEN_ID 配置不正确")

    auth = request_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret}
    )
    if auth.get("code") != 0:
        raise RuntimeError("获取飞书 Token 失败")

    payload = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": message}, ensure_ascii=False),
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, key))
    }
    # Persist ambiguity protection after auth, immediately before the side effect.
    before_send()
    result = request_json(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        payload,
        auth["tenant_access_token"]
    )
    if result.get("code") != 0:
        raise RuntimeError("飞书发送失败，错误码：" + str(result.get("code")))
    message_id = result.get("data", {}).get("message_id")
    if not message_id:
        raise RuntimeError("飞书未返回消息 ID，发送结果需人工核对")
    return message_id


def format_card(index):
    method_index = index % len(METHODS)
    round_index = index // len(METHODS)
    scenario_index = (
        round_index * 7 + method_index * 3
    ) % len(SCENARIOS)

    title, core, prompt, warning = METHODS[method_index]
    task = SCENARIOS[scenario_index]

    return (
        f"【{title}】\n"
        "来源：原有整理策略库｜非最新发布；无原始发布日期\n"
        f"核心原理：{core}\n"
        f"应用场景：{task}\n"
        f"使用方法：将以下提示词发给AI，"
        f"结合你的真实情况继续互动。\n"
        f"可复制提示词：{prompt.replace('{task}', task)}\n"
        f"注意事项：{warning}"
    )


def format_new(method, today):
    source = method["source"]
    age = (date.fromisoformat(today) - date.fromisoformat(source["published_at"])).days
    label = "近7天发布·已审核" if age <= 7 else "近期实践·非近7天发布·已审核"
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(method["steps"], 1))
    return (
        f"【{method['name']}】{label}\n"
        f"核心原理：{method['principle']}\n"
        f"应用场景：{'；'.join(method['scenarios'])}\n"
        f"操作步骤：\n{steps}\n"
        f"示例提示词（人工整理，非原文引用）：{method['example_prompt']}\n"
        f"原始来源：{source['name']}｜{source['url']}\n"
        f"原文发布日期：{source['published_at']}"
    )


def choose_methods(state, verified, today):
    used = set(state["sent_ids"])
    names = set(state["sent_names"])
    chosen = []
    for method in verified:
        key = method["method_key"]
        normalized = name_key(method["name"])
        if method["id"] in used or key in used or normalized in names:
            continue
        chosen.append({"id": method["id"], "method_key": key, "name_key": normalized,
                       "card": format_new(method, today), "kind": "verified"})
        used.update((method["id"], key))
        names.add(normalized)
        if len(chosen) == 3:
            return chosen
    for i, (title, *_rest) in enumerate(METHODS):
        key = builtin_id(i)
        normalized = name_key(title)
        if key in used or normalized in names:
            continue
        chosen.append({"id": key, "method_key": key, "name_key": normalized,
                       "card": format_card(i), "kind": "builtin"})
        used.add(key)
        names.add(normalized)
        if len(chosen) == 3:
            break
    return chosen


def build_pending(state, verified, today):
    previous = state.get("pending")
    if previous and previous.get("status") in ("attempted", "unknown"):
        raise RuntimeError("上次发送结果不明，已暂停；请核对飞书后使用 --resolve，禁止自动补发")
    if state.get("last_sent") == today:
        return None
    if previous:
        if previous.get("status") != "prepared":
            raise ValueError("未知发送状态，拒绝自动重试")
        if previous["date"] == today:
            return previous
    selected = choose_methods(state, verified, today)
    if not selected:
        return None
    message = (f"🧠 每日AI实用策略 | {today}\n"
               f"今天精选{len(selected)}种AI使用方法与实践案例\n\n"
               + "\n\n────────────\n\n".join(item["card"] for item in selected)
               + "\n\n说明：自动发现的原文须审核后进入推送；新方法不足时补充原策略。"
                 "原策略及较早实践均不代表当天新发布。")
    if len(selected) < 3:
        message += "\n可用且未推送的方法不足3条，本次只发送以上条目，避免重复。"
    # Feishu text messages have a byte limit; do not truncate away source evidence.
    if len(json.dumps({"text": message}, ensure_ascii=False).encode("utf-8")) > 19000:
        raise ValueError("消息过长，请缩短审核内容后再发送")
    return {"status": "prepared", "date": today, "key": f"feishu-ai-daily-{today}",
            "items": [{k: v for k, v in item.items() if k != "card"} for item in selected],
            "message": message}


def checkpoint(state, path=STATE_FILE, durable=False):
    write_json(path, state)
    if durable:
        subprocess.run([sys.executable, str(ROOT / "persist.py"), "--state-only"],
                       cwd=ROOT, check=True)


def finish_delivery(state, message_id):
    pending = state["pending"]
    state["sent_ids"] = sorted(set(state["sent_ids"]) | {
        value for item in pending["items"] for value in (item["id"], item["method_key"])})
    state["sent_names"] = sorted(set(state["sent_names"]) | {
        item["name_key"] for item in pending["items"]})
    state["last_sent"] = pending["date"]
    state["next_index"] = next((i for i in range(len(METHODS))
                                  if builtin_id(i) not in state["sent_ids"]), len(METHODS))
    state["deliveries"].append({"date": pending["date"], "key": pending["key"],
                                "ids": [item["id"] for item in pending["items"]],
                                "message_id": message_id})
    state["pending"] = None


def deliver(state, today, path=STATE_FILE, sender=send_to_feishu, durable=False):
    pending = state.get("pending")
    if not pending:
        print("没有待发送批次（今天可能已推送，或策略库已耗尽）")
        return
    if state.get("last_sent") == today:
        raise RuntimeError("今天已有发送记录，拒绝再次发送")
    if pending.get("status") != "prepared" or pending.get("date") != today:
        raise RuntimeError("批次已过期或结果不明，拒绝自动重发")

    def before_send():
        pending["status"] = "attempted"
        pending["attempted_at"] = datetime.now(timezone.utc).isoformat()
        checkpoint(state, path, durable)

    try:
        message_id = sender(pending["message"], pending["key"], before_send)
        if pending["status"] != "attempted" or not message_id:
            raise RuntimeError("发送器未完成检查点或未提供回执")
    except Exception:
        if pending["status"] == "attempted":
            pending["status"] = "unknown"
            checkpoint(state, path)
        raise
    finish_delivery(state, message_id)
    checkpoint(state, path)
    print("飞书已返回成功回执，已保存方法 ID 去重记录")


def main():
    parser = argparse.ArgumentParser(description="AI策略推送；默认只预览，不调用飞书")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--deliver", action="store_true")
    modes.add_argument("--resolve", choices=("sent", "not_sent"))
    modes.add_argument("--list-builtins", action="store_true")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    if args.list_builtins:
        for i, method in enumerate(METHODS):
            print(f"{builtin_id(i)}  {method[0]}")
        return
    state = load_state()
    today = datetime.now(SHANGHAI).date().isoformat()
    if args.resolve:
        pending = state.get("pending")
        if not args.note.strip() or not pending or pending.get("status") not in ("attempted", "unknown"):
            raise ValueError("只有结果不明批次可处理，且必须 --note 记录飞书核对依据")
        state.setdefault("resolutions", []).append({"key": pending["key"], "result": args.resolve,
                                                     "note": args.note, "at": datetime.now(timezone.utc).isoformat()})
        if args.resolve == "sent":
            finish_delivery(state, "manually-confirmed")
        else:
            state["pending"] = None
        checkpoint(state)
        print("已记录人工核对结果；请提交 state.json 后再运行 Actions")
    elif args.deliver:
        deliver(state, today, durable=os.getenv("REQUIRE_DURABLE_STATE") == "1")
    else:
        pending = build_pending(state, load_verified(), today)
        if args.prepare:
            state["pending"] = pending
            checkpoint(state)
            print("已准备批次" if pending else "今天已推送或无未推送方法；没有新批次")
        elif pending:
            print(pending["message"])
            print("\n[预览：未调用飞书，未修改发送状态]")
        else:
            print("今天已推送或无未推送方法；不会重复发送")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print HTTP bodies, credentials, recipient IDs, or request objects.
        detail = str(exc) if type(exc) in (ValueError, RuntimeError) else "请检查状态检查点和 Actions 步骤"
        print(f"推送未完成：{type(exc).__name__}：{detail}", file=sys.stderr)
        sys.exit(1)

