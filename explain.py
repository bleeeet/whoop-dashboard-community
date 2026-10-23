#!/usr/bin/env python3
"""每个指标的解读文案。

工作台上那些数字，光看数值不知道是好是坏。这里按当前数值分档，
给出「你现在处在哪一档、这个指标到底在量什么」。

排版规则：一屏之内能扫完。每段固定为
    指标名（大标题）→ 当前值与所在档 → 一句话结论 → 档位条 → 逐条小标题
不写成段落 —— 大段文字在手机上读不下去，条目化才能一眼找到想看的那条。

分档口径来自 WHOOP 官方对各指标的定义。凡是因人而异的指标
（HRV、静息心率、皮肤温度），一律拿本人近 7 日均值当基线比，
不用绝对值下结论 —— 那样得出的结论对个体没有意义。

这些是帮你读懂数据的说明，不是医疗建议。
"""


def _sec(name, value, zone, tone, lead, scale=None, rows=(), notes=()):
    """一段解读。tone 决定强调色：g 好 / y 一般 / o 该留意 / r 差 / b 中性蓝 / n 无色。"""
    head = ""
    if value:
        z = f"<em>{zone}</em>" if zone else ""
        head = f'<div class="exh"><b>{value}</b>{z}</div>'
    body = f'<p class="exl">{lead}</p>' if lead else ""
    if scale:
        labels, idx = scale
        cells = "".join(f'<i class="on">{t}</i>' if i == idx else f"<i>{t}</i>"
                        for i, t in enumerate(labels))
        body += f'<div class="exs">{cells}</div>'
    if rows:
        body += ('<dl class="exd">'
                 + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows if v)
                 + "</dl>")
    body += "".join(f'<p class="exn">{n}</p>' for n in notes if n)
    return f'<div class="ex t-{tone}"><h5>{name}</h5>{head}{body}</div>'


def _base_row(base, unit, digits=0):
    return ("你的基线", f"近 7 日均值 {base:.{digits}f}{unit}") if base else None


def _rows(*items):
    return [x for x in items if x]


# ---------- 恢复与负荷 ----------

def recovery(v):
    if v is None:
        return None
    if v >= 67:
        zone, tone, idx = "绿区", "g", 2
        lead = "身体有余力。适合上强度，或者推进最费神的那件事。"
    elif v >= 34:
        zone, tone, idx = "黄区", "y", 1
        lead = "中间状态。维持日常强度没问题，别急着冲极限。"
    else:
        zone, tone, idx = "红区", "r", 0
        lead = "身体还没缓过来。今天优先轻活动和睡眠，硬练性价比很低。"
    return _sec(
        "恢复分", f"{v:.0f}%", zone, tone, lead,
        (["红区 0–33", "黄区 34–66", "绿区 67–100"], idx),
        [("在量什么", "身体<b>此刻还剩多少余力</b>，不是「昨晚睡得好不好」的评分。"),
         ("怎么算的", "HRV、静息心率、睡眠、呼吸频率四项合成，<b>HRV 权重最大</b>。"),
         ("量的是哪套系统", "自主神经 —— 身体此刻偏向「随时能行动」还是「正在修自己」。"),
         ("什么会压低它", "熬夜、饮酒、生病、长期压力。")])


def strain(v):
    if v is None:
        return None
    if v < 10:
        zone, tone, idx = "轻松", "b", 0
        lead = "相当于以坐着为主、没有正式训练的一天。"
    elif v < 14:
        zone, tone, idx = "中等", "b", 1
        lead = "有实打实的活动量，属于能天天维持的强度。"
    elif v < 18:
        zone, tone, idx = "偏高", "y", 2
        lead = "训练量不小，接下来记得给身体留出恢复时间。"
    else:
        zone, tone, idx = "全力", "o", 3
        lead = "接近个人上限的一天，之后通常需要一到两天低强度。"
    return _sec(
        "负荷 Strain", f"{v:.1f}", zone, tone, lead,
        (["轻松 0–9", "中等 10–13", "偏高 14–17", "全力 18–21"], idx),
        [("在量什么", "这一天心血管一共扛了多少负荷，按心率在各强度区间的停留时长累积。"),
         ("只涨不跌", "从 0 点一路加到此刻，所以<b>晚上看总比早上大</b>。"),
         ("是对数刻度", "14 涨到 15 比 4 涨到 5 费力得多，<b>不能做加减或求平均</b>。"),
         ("因人而异", "同样一段跑步，体能不同的人拿到的 Strain 并不一样。")])


# ---------- 睡眠 ----------

def sleep_perf(v):
    if v is None:
        return None
    if v >= 85:
        zone, tone, idx = "充足", "g", 2
        lead = "基本睡够了身体当天需要的量。"
    elif v >= 70:
        zone, tone, idx = "偏少", "y", 1
        lead = "短期没问题，连续几天这样就会累积成睡眠债。"
    else:
        zone, tone, idx = "明显不足", "o", 0
        lead = "今天的恢复分、专注力大概率都会受影响。"
    return _sec(
        "睡眠表现", f"{v:.0f}%", zone, tone, lead,
        (["不足 <70", "偏少 70–84", "充足 85+"], idx),
        [("怎么算的", "<b>实际睡着的时间 ÷ 身体当天需要的睡眠</b>。"),
         ("分母不是 8 小时", "按基础需求，加上前几天欠的睡眠债、加上近期训练负荷算出来。"),
         ("所以会出现", "练得狠的那几天需求自动调高，同样睡 7 小时，分数反而更低。")])


def sleep_eff(v):
    if v is None:
        return None
    if v >= 85:
        zone, tone, idx = "正常", "g", 1
        lead = "躺下之后基本都在睡。"
    else:
        zone, tone, idx = "偏低", "y", 0
        lead = "躺着的时间里有不少没睡着 —— 入睡慢，或者夜里醒的次数多。"
    return _sec(
        "睡眠效率", f"{v:.0f}%", zone, tone, lead,
        (["偏低 <85", "正常 85+"], idx),
        [("怎么算的", "<b>真正睡着的时间 ÷ 躺在床上的总时间</b>。"),
         ("和睡眠表现的区别", "表现问「够不够」，效率问「躺着的时间有没有浪费」。"),
         ("举个例", "睡够 8 小时但效率只有 70%，意味着你在床上耗了 11 小时。")])


def stages(deep_ms, rem_ms, light_ms, awake_ms):
    total = (deep_ms or 0) + (rem_ms or 0) + (light_ms or 0)
    if not total:
        return None
    pd, pr = (deep_ms or 0) / total * 100, (rem_ms or 0) / total * 100
    notes = []
    if pd < 13:
        notes.append("深睡占比低于常见的 13–23%。常见原因：睡前饮酒、睡得太晚、房间太热。")
    if pr < 20:
        notes.append("快速眼动低于常见的 20–25%。它集中在后半夜，睡得太短最先砍掉的就是这段。")
    return _sec(
        "睡眠分期", f"深睡 {pd:.0f}%", f"快速眼动 {pr:.0f}%", "n",
        "占实际睡着时间的比例。四个阶段各管一件事：",
        None,
        [("深睡（慢波）", "<b>身体的修理时段</b> —— 组织修复、生长激素、免疫系统。集中在前半夜。"),
         ("快速眼动 REM", "<b>大脑的整理时段</b> —— 巩固记忆、消化情绪。集中在后半夜。"),
         ("浅睡", "占比最大，是各阶段之间的过渡，<b>不是「没睡好」</b>。"),
         ("夜间清醒", "一晚醒几次属于正常，多数根本不会记得。")],
        notes)


def sleep_debt(minutes):
    if minutes is None:
        return None
    if minutes <= 0:
        value, zone, tone = "已还清", "", "g"
        lead = "实际睡的比身体需要的还多一点。"
    elif minutes < 60:
        value, zone, tone = f"{int(minutes)} 分钟", "缺口不大", "y"
        lead = "正常作息一两天就能补回来。"
    else:
        h, m = divmod(int(minutes), 60)
        value, zone, tone = f"{h}小时{m}分", "缺口明显", "o"
        lead = "已经比较明显，会压着恢复分上不去。"
    return _sec(
        "睡眠债", value, zone, tone, lead, None,
        [("是什么", "最近几天「需要的睡眠」和「实际睡的」之间的<b>累积差值</b>，不是某一晚的欠账。"),
         ("怎么补", "靠连续几天稍微多睡一点，<b>比周末一次性补觉更有效</b>。")])


# ---------- 生理指标（都要跟自己的基线比） ----------

def _dev(v, base, unit="", digits=0):
    """跟近 7 日均值比，返回 (档位文字, 差值)。基线缺失时 (None, None)。"""
    if v is None or not base:
        return None, None
    d = v - base
    if abs(d) < (0.5 if digits == 0 else 0.05):
        return "与基线持平", d
    return f"{'高于' if d > 0 else '低于'}基线 {abs(d):.{digits}f}{unit}", d


def hrv(v, base):
    if v is None:
        return None
    zone, d = _dev(v, base, "ms")
    if zone is None:
        zone, tone, lead = "暂无基线", "n", "还没有足够的历史数据来比较。"
    elif d >= 0:
        tone = "g"
        lead = "通常说明恢复得不错。"
    else:
        tone = "y"
        lead = "常见于训练后疲劳、睡眠不足、压力大、饮酒，或者将要生病。"
    return _sec(
        "心率变异性 HRV", f"{v:.0f}ms", zone, tone, lead, None,
        _rows(("在量什么", "<b>相邻两次心跳的时间间隔的波动程度</b>。"),
              ("高说明什么", "间隔忽长忽短 = 身体放松、随时能应变。"),
              ("低说明什么", "间隔像节拍器一样规整 = 身体在紧绷或疲劳。"),
              ("别跟别人比", "有人常年 30ms，有人常年 120ms，都正常。<b>只看相对自己的基线</b>。"),
              _base_row(base, "ms")))


def rhr(v, base):
    if v is None:
        return None
    zone, d = _dev(v, base, " bpm")
    if zone is None:
        zone, tone, lead = "暂无基线", "n", "还没有足够的历史数据来比较。"
    elif d <= 0:
        tone = "g"
        lead = "是个好信号，通常意味着恢复良好。"
    elif d < 5:
        tone = "n"
        lead = "小幅波动，属正常范围。"
    else:
        tone = "o"
        lead = "升高得比较明显。常见于前一天训练量大、睡眠不足、脱水、饮酒，或身体正在对抗感染。"
    return _sec(
        "静息心率", f"{v:.0f} bpm", zone, tone, lead, None,
        _rows(("在量什么", "<b>身体完全静止时的心跳次数</b>，手环取的是夜间睡眠时段。"),
              ("和 HRV 的关系", "通常反向变化：恢复得好时 HRV 升、静息心率降；累了或生病时反过来。"),
              _base_row(base, " bpm")))


def spo2(v):
    if v is None:
        return None
    if v >= 95:
        zone, tone, idx = "正常", "g", 1
        lead = "在常见的 95–100% 范围内。"
    else:
        zone, tone, idx = "偏低", "y", 0
        lead = "偶尔一次多半是手环整夜佩戴位置或松紧的问题；连续多天偏低值得找医生看看。"
    return _sec(
        "血氧饱和度", f"{v:.1f}%", zone, tone, lead,
        (["偏低 <95%", "正常 95–100%"], idx),
        [("在量什么", "血液里<b>携氧血红蛋白的百分比</b>，手环在睡眠时段用光学方式估算。"),
         ("怎么看", "腕式测量精度不如医用指夹式，<b>看趋势比看单次数值可靠</b>。")],
        ["这不是诊断工具。身体有不适请以医生判断为准。"])


def skin_temp(v, base):
    if v is None:
        return None
    zone, d = _dev(v, base, "°C", 1)
    if zone is None:
        zone, tone, lead = "暂无基线", "n", "还没有足够的历史数据来比较。"
    elif abs(d) < 0.5:
        tone = "g"
        lead = "在日常波动范围内。"
    elif d > 0:
        tone = "o"
        lead = "偏离得比较明显。常见于免疫系统正在应对什么、饮酒、房间偏热，女性也可能是生理周期所致。"
    else:
        tone = "y"
        lead = "偏离得比较明显，多数与睡眠环境温度有关。"
    return _sec(
        "皮肤温度", f"{v:.1f}°C", zone, tone, lead, None,
        _rows(("不是体温", "这是<b>手腕皮肤表面的温度</b>，和口腔、耳温没有可比性。"),
              ("看什么", "只有<b>相对你自己基线的偏移</b>有意义，绝对值说明不了问题。"),
              ("为什么值得看", "身体在对抗感染时，往往在你有自觉症状之前，这个数就先动了。"),
              _base_row(base, "°C", 1)))


# ---------- 压力（本站推算，非 WHOOP 官方值） ----------

def stress(v, hrv_v, hrv_base, rhr_v, rhr_base):
    if v is None:
        return None
    if v < 1:
        zone, tone, idx = "低压力", "g", 0
        lead = "身体没有明显的应激信号。"
    elif v < 2:
        zone, tone, idx = "中等压力", "y", 1
        lead = "有一些应激迹象，可能来自训练、睡眠不足或情绪负荷。"
    else:
        zone, tone, idx = "高压力", "o", 2
        lead = "身体处在比较紧绷的状态，今天适合减量、早睡。"
    how = None
    if hrv_v is not None and hrv_base and rhr_v is not None and rhr_base:
        how = ("怎么来的",
               f"HRV {hrv_v:.0f} 对基线 {hrv_base:.0f}ms，静息心率 {rhr_v:.0f} 对基线 "
               f"{rhr_base:.0f} bpm。HRV 掉得越多、静息心率抬得越高，推算出的压力越高。")
    return _sec(
        "压力（推算）", f"{v:.1f}", zone, tone, lead,
        (["低 0–1", "中 1–2", "高 2–3"], idx),
        _rows(how,
              ("不是 App 那个分", "WHOOP 开放接口不提供压力数据，也不提供逐分钟心率。"),
              ("那这个哪来的", "用接口给得到的 HRV 与静息心率，按它们偏离个人基线的程度推算。"),
              ("怎么用", "量程沿用 0–3 只为方便对照，<b>数值不会和 App 一致</b>。当趋势看：连续几天走高，说明身体确实在承压。")))
