
import os
import json
import uuid
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

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

TOTAL = len(METHODS) * len(SCENARIOS)
STATE_FILE = Path("state.json")


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


def send_to_feishu(message, key):
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
    result = request_json(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        payload,
        auth["tenant_access_token"]
    )
    if result.get("code") != 0:
        raise RuntimeError(
            "飞书发送失败：" + str(result.get("code")) +
            " " + str(result.get("msg"))
        )


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
        f"核心原理：{core}\n"
        f"应用场景：{task}\n"
        f"使用方法：将以下提示词发给AI，"
        f"结合你的真实情况继续互动。\n"
        f"可复制提示词：{prompt.replace('{task}', task)}\n"
        f"注意事项：{warning}"
    )


def main():
    today = datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).strftime("%Y-%m-%d")

    state = (
        json.loads(STATE_FILE.read_text("utf-8"))
        if STATE_FILE.exists() else {}
    )

    if state.get("last_sent") == today:
        print("今天已推送，避免重复发送")
        return

    index = int(state.get("next_index", 0))

    if index + 3 > TOTAL:
        if not state.get("exhausted_notice"):
            send_to_feishu(
                "AI策略库已全部推送，请补充新的策略后继续。",
                "feishu-ai-daily-exhausted"
            )
            state["exhausted_notice"] = True
            STATE_FILE.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        return

    cards = [format_card(i) for i in range(index, index + 3)]
    message = (
        f"🧠 每日AI实用策略 | {today}\n"
        "今天精选3种AI使用方法与实践案例\n\n"
        + "\n\n────────────\n\n".join(cards)
        + "\n\n说明：来自整理的策略库及场景迁移，"
          "不代表当天新发布的AI研究。"
    )

    send_to_feishu(message, f"feishu-ai-daily-{today}")

    state["next_index"] = index + 3
    state["last_sent"] = today
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print("成功发送3条AI使用策略至飞书个人私聊")


if __name__ == "__main__":
    main()

