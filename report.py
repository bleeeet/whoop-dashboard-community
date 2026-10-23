#!/usr/bin/env python3
"""读 data/ 生成全中文的 WHOOP 健康工作台（自包含单文件 HTML，零 JS）。"""

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone

import explain

DISPLAY_TZ = None  # 生成时间按运行环境本地时区；记录按 WHOOP timezone_offset 显示

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(BASE, "data")
DEFAULT_OUT = os.path.join(BASE, "publish", "index.html")

# --- 配色：WHOOP 同款暗色 ---
BG, CARD, LINE = "#0A0A0A", "#16181A", "#26292C"
FG, DIM = "#F5F5F5", "#8A8F94"
GREEN, YELLOW, RED = "#16EC06", "#FFDE00", "#FF0026"
BLUE = "#0093E7"
DEEP, REM, LIGHT, AWAKE = "#3B5BFF", "#7B61FF", "#4A5568", "#FF6B6B"
ZONES = ["#2D3238", "#3E6B8A", "#0093E7", "#FFDE00", "#FF8C00", "#FF0026"]

SPORTS = {
    "Running": "跑步", "Cycling": "骑行", "Weightlifting": "力量训练", "Swimming": "游泳",
    "Walking": "步行", "Hiking/Rucking": "徒步", "Yoga": "瑜伽", "Functional Fitness": "综合体能",
    "Boxing": "拳击", "Basketball": "篮球", "Badminton": "羽毛球", "Tennis": "网球",
    "Elliptical": "椭圆机", "Rowing": "划船机", "Pilates": "普拉提", "Stairmaster": "爬楼机",
    "Dance": "舞蹈", "Golf": "高尔夫", "Soccer": "足球", "Volleyball": "排球",
    "Table Tennis": "乒乓球", "Skiing": "滑雪", "Snowboarding": "单板滑雪", "Climbing": "攀岩",
    "Martial Arts": "武术", "Jiu Jitsu": "巴西柔术", "Meditation": "冥想", "Stretching": "拉伸",
    "Pickleball": "匹克球", "HIIT": "高强度间歇", "Spin": "动感单车", "Jogging": "慢跑",
    "Activity": "日常活动", "Other": "其他", "Powerlifting": "力量举",
}
SPORTS = {k.lower(): v for k, v in SPORTS.items()}   # 接口 v2 给的 sport_name 是小写


# ---------- 通用小工具 ----------

def safe_div(a, b, default=0.0):
    return a / b if b else default


def fmt_dur(ms):
    if ms is None:
        return "—"
    m = round(ms / 60000)
    return f"{m // 60}h{m % 60:02d}m"


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def num(v, digits=0, suffix=""):
    """None 一律渲染成占位符，绝不让 None/NaN 漏进 HTML。"""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    return (f"{v:.{digits}f}" if digits else f"{round(v):d}") + suffix


OPT, SUF, POOR = GREEN, "#8A8F94", "#FF8C00"


def perf_level(v):
    return None if v is None else (2 if v >= 85 else (1 if v >= 70 else 0))


def rec_level(v):
    return None if v is None else (2 if v >= 67 else (1 if v >= 34 else 0))


def strain_level(v):
    return None if v is None else (0 if v < 10 else (1 if v < 14 else 2))


def zonebar(level, cols=None):
    """三段状态条：不够 / 一般 / 够好，只把当前那一档点亮。

    App 里最好认的组件——不用读数字就知道这个值算不算好。
    """
    if level is None:
        return ""
    cols = cols or (POOR, SUF, OPT)
    segs = ""
    for i in range(3):
        c = cols[i] if i == level else "rgba(255,255,255,.12)"
        segs += f'<i style="background:{c}"></i>'
    return f'<span class="zb">{segs}</span>'


def perf_color(v):
    """睡眠表现：够好（绿）/ 一般（灰）/ 不够（橙），跟 App 的三档对齐。"""
    if v is None:
        return DIM
    return OPT if v >= 85 else (SUF if v >= 70 else POOR)


def dev_color(v, base, tol, worse="high"):
    """相对个人基线偏离多少才值得提醒。tol 是容忍比例，超过两倍报警色。

    只给真正需要看一眼的数上色 —— 全都花花绿绿等于没有重点。
    """
    if v is None or not base:
        return None
    d = (v - base) / base
    if worse == "low":
        d = -d
    return POOR if d > tol * 2 else (YELLOW if d > tol else None)


def stress_proxy(hrv, hrv_base, rhr, rhr_base):
    """用 HRV 与静息心率相对个人基线的偏离，推算 0-3 的每日压力代理值。

    WHOOP App 的压力分同样基于 HRV 和心率，但用的是全天连续采样与自有算法。
    开放接口既不给那个数值、也不给逐分钟心率，所以这里只能按每天一个点推算。
    量程沿用 0-3 是为了方便对照，两者不会一致 —— 页面上必须标明是本站推算。
    """
    if hrv is None or not hrv_base or rhr is None or not rhr_base:
        return None
    d_hrv = (hrv_base - hrv) / hrv_base          # HRV 掉下来为正
    d_rhr = (rhr - rhr_base) / rhr_base          # 静息心率抬上去为正
    # 各自归一：HRV 掉三成、静息心率抬一成二，各算满一档
    z = (max(-1.0, min(1.0, d_hrv / 0.30))
         + max(-1.0, min(1.0, d_rhr / 0.12)))
    return max(0.0, min(3.0, 1.0 + z))           # 基线状态落在 1.0 附近


def stress_zone(v):
    if v is None:
        return "暂无"
    return "低" if v < 1 else ("中" if v < 2 else "高")


def stress_color(v):
    if v is None:
        return DIM
    return OPT if v < 1 else (YELLOW if v < 2 else POOR)


def stress_series(days):
    """逐日压力代理值，返回 {日期: 值}。

    每天的基线只取它之前最多 7 天 —— 用当天或之后的数据当基线，
    等于拿答案算答案，趋势就没意义了。
    """
    f_h = lambda x: (d_score(x, "recovery") or {}).get("hrv_rmssd_milli")
    f_r = lambda x: (d_score(x, "recovery") or {}).get("resting_heart_rate")
    out = {}
    for i, d in enumerate(days):
        rs = d_score(d, "recovery") or {}
        hist = days[max(0, i - 7):i]
        out[d["date"]] = stress_proxy(
            rs.get("hrv_rmssd_milli"), avg([f_h(x) for x in hist]),
            rs.get("resting_heart_rate"), avg([f_r(x) for x in hist]))
    return out


def spo2_color(v):
    if v is None:
        return None
    return POOR if v < 93 else (YELLOW if v < 95 else None)


def temp_color(v, base):
    """皮温看的是与个人基线的绝对偏离，不是比例。"""
    if v is None or base is None:
        return None
    d = abs(v - base)
    return POOR if d > 1.0 else (YELLOW if d > 0.5 else None)


def eff_color(v):
    return None if v is None or v >= 85 else POOR


def rec_color(score):
    if score is None:
        return DIM
    return GREEN if score >= 67 else (YELLOW if score >= 34 else RED)


def rec_zone(score):
    if score is None:
        return "暂无"
    return "绿区" if score >= 67 else ("黄区" if score >= 34 else "红区")


def pearson(pairs):
    """皮尔逊相关系数。样本不足或方差为零返回 None。"""
    pts = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    d = math.sqrt(sxx * syy)
    return sxy / d if d else None


def r_words(r):
    if r is None:
        return "相关性无法计算"
    a = abs(r)
    lvl = "强" if a >= 0.6 else ("中等" if a >= 0.35 else ("弱" if a >= 0.2 else "几乎无"))
    return f"{lvl}{'正' if r > 0 else '负'}相关" if a >= 0.2 else "几乎无相关"


# ---------- 数据层 ----------

def load(data_dir):
    out = {}
    for name, default in (("cycles", []), ("recovery", []), ("sleep", []),
                          ("workouts", []), ("profile", {}), ("body", {})):
        try:
            with open(os.path.join(data_dir, name + ".json")) as f:
                out[name] = json.load(f)
        except (OSError, ValueError):
            out[name] = default

    return out


def parse_ts(iso_str, offset="+08:00"):
    """UTC 时间戳 + 记录自带时区偏移 -> 当地 datetime。"""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    sign = -1 if offset.startswith("-") else 1
    hh, _, mm = offset.lstrip("+-").partition(":")
    delta = timedelta(hours=int(hh), minutes=int(mm or 0)) * sign
    return (dt + delta).replace(tzinfo=None)


def build_days(raw):
    """以 cycle 为骨架按日索引。recovery/sleep 靠 cycle_id 关联，workout 靠自身时间。"""
    rec_by_cycle = {r["cycle_id"]: r for r in raw["recovery"] if "cycle_id" in r}
    sleep_by_cycle, naps_by_cycle = {}, {}
    for s in raw["sleep"]:
        cid = s.get("cycle_id")
        if cid is None:
            continue
        if s.get("nap"):
            naps_by_cycle.setdefault(cid, []).append(s)
        else:
            sleep_by_cycle[cid] = s

    days = []
    for c in sorted(raw["cycles"], key=lambda x: x.get("start") or ""):
        local = parse_ts(c.get("start"), c.get("timezone_offset", "+08:00"))
        if local is None:
            continue
        # cycle 从入睡那刻算起；后移 6 小时，让 18 点后入睡的那一轮归到醒来那天，与 App 一致
        dt = local + timedelta(hours=6)
        d = {
            "date": dt.strftime("%Y-%m-%d"),
            "dt": dt,
            "cycle": c,
            "recovery": rec_by_cycle.get(c["id"]),
            "sleep": sleep_by_cycle.get(c["id"]),
            "naps": naps_by_cycle.get(c["id"], []),
            "workouts": [],
        }
        days.append(d)

    # workout 按时间落进所属 cycle，不按日历日：凌晨入睡前的训练属于前一轮
    for w in raw["workouts"]:
        ws = w.get("start") or ""
        for d in reversed(days):
            if ws and (d["cycle"].get("start") or "") <= ws:
                d["workouts"].append(w)
                break

    return days


# --- 从 Day 里取指标，缺失一律 None ---

def d_score(day, kind):
    obj = day.get(kind)
    return (obj or {}).get("score") or None


def d_strain(day):
    return (d_score(day, "cycle") or {}).get("strain")


def d_rec(day):
    return (d_score(day, "recovery") or {}).get("recovery_score")


def d_stage(day):
    return (d_score(day, "sleep") or {}).get("stage_summary") or None


def d_sleep_total(day):
    st = d_stage(day)
    if not st:
        return None
    return (st.get("total_light_sleep_time_milli", 0)
            + st.get("total_slow_wave_sleep_time_milli", 0)
            + st.get("total_rem_sleep_time_milli", 0))


def d_sleep_need(day):
    sn = (d_score(day, "sleep") or {}).get("sleep_needed")
    if not sn:
        return None
    return (sn.get("baseline_milli", 0) + sn.get("need_from_sleep_debt_milli", 0)
            + sn.get("need_from_recent_strain_milli", 0) + sn.get("need_from_recent_nap_milli", 0))


def d_sleep_debt(day):
    """WHOOP 算好的累积睡眠债（App 里的 Sleep Debt），不是「需求 − 昨晚实睡」。"""
    return ((d_score(day, "sleep") or {}).get("sleep_needed") or {}).get("need_from_sleep_debt_milli")


def d_consistency(day):
    """前一晚没记录时接口给 0.0 而不是 null，按缺失处理，别画成 0%。"""
    return (d_score(day, "sleep") or {}).get("sleep_consistency_percentage") or None


def d_strain_done(day):
    """进行中的 cycle 负荷还在涨，拿去算平均会把均值拉低。"""
    return d_strain(day) if (day.get("cycle") or {}).get("end") else None


# ---------- SVG 原语 ----------

W, H = 880, 190          # 图表默认画布
PAD_L, PAD_R, PAD_B, PAD_T = 12, 40, 34, 10


def empty_hint(text):
    return ('<div class="empty">'
            '<svg viewBox="0 0 24 24" class="eico"><path d="M3 21h18M6 21V11M12 21V5M18 21v-6"/></svg>'
            f'<b>暂无数据</b><span>{esc(text)}</span></div>')


def canvas_w(n):
    """画布宽度随数据点数走。

    7 个点和 90 个点挤在同一个 880 宽画布里，前者被拉得稀稀拉拉，
    后者又非横滑不可。低于 560 时 svg_open 会把 min-width 一并调小，
    手机上就不用横向滚动了。
    """
    return max(300, min(W, PAD_L + PAD_R + n * 34))


def _xs(n, w=W):
    """n 根柱子的中心 x 与柱宽。"""
    if n <= 0:
        return [], 0
    span = w - PAD_L - PAD_R
    step = span / n
    bw = min(30.0, max(3.0, step * 0.62))
    return [PAD_L + step * (i + 0.5) for i in range(n)], bw


def _band(days, xs, bw):
    """最后一天的高亮竖条。一眼看出哪根是「今天」，不用去数横轴。"""
    if not days:
        return ""
    i = len(days) - 1
    w = max(bw * 2.0, 24)
    return (f'<rect x="{xs[i] - w / 2:.1f}" y="{PAD_T - 5:.1f}" width="{w:.1f}" '
            f'height="{H - PAD_T - 1:.1f}" rx="5" fill="{FG}" opacity="0.06"/>')


def _sheen(x, y, w, h):
    """柱子顶部的一层高光。App 的柱子都是上浅下深，纯色柱看着扁。"""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="url(#sheen)" rx="1.5"/>')


def _vlabel(x, y, text, color):
    """把数值直接标在柱顶，省掉「对着纵轴数格子」这一步。"""
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'class="vlab" fill="{color}">{text}</text>')


def _grid(vmax, ticks=4, unit="", w=W):
    out = []
    plot_h = H - PAD_T - PAD_B
    for i in range(ticks + 1):
        v = vmax * i / ticks
        y = PAD_T + plot_h * (1 - i / ticks)
        out.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{w - PAD_R}" y2="{y:.1f}" '
                   f'stroke="{LINE}" stroke-width="1"/>')
        out.append(f'<text x="{w - PAD_R + 7}" y="{y + 4:.1f}" text-anchor="start" '
                   f'class="ax">{v:.0f}{unit}</text>')
    return "".join(out)


def _xlabels(days, xs):
    """稀疏日期标签，最多 7 个，避免挤成一团。"""
    n = len(days)
    if n == 0:
        return ""
    step = max(1, round(n / 7))
    out = []
    for i in sorted(range(n - 1, -1, -step)):    # 从最后一天往回取，保证「今天」有标签
        cls = "ax today" if i == n - 1 else "ax"
        dt = days[i]["dt"]
        out.append(f'<text x="{xs[i]:.1f}" y="{H - 17}" text-anchor="middle" class="{cls}">'
                   f'周{"一二三四五六日"[dt.weekday()]}</text>'
                   f'<text x="{xs[i]:.1f}" y="{H - 5}" text-anchor="middle" class="{cls}">'
                   f'{dt.day}</text>')
    return "".join(out)


def svg_open(extra="", w=W):
    # 不设 preserveAspectRatio：默认等比缩放，窄屏下才不会把文字和柱子压扁
    fits = " fits" if w <= 340 else ""      # 窄画布在手机上放得下，不必提示滑动
    return (f'<div class="chartbox{fits}"><svg viewBox="0 0 {w} {H}" class="chart" '
            f'style="min-width:{min(w, 560)}px;max-width:{round(w * 1.3)}px">{extra}')


def svg_stack(days, hint):
    """睡眠分期堆叠竖条 + 睡眠需求虚线。"""
    vals = [(d, d_stage(d)) for d in days]
    if not any(st for _, st in vals):
        return empty_hint(hint)
    tops = [(d_sleep_total(d) or 0) + (d_stage(d) or {}).get("total_awake_time_milli", 0)
            for d in days]
    needs = [d_sleep_need(d) for d in days]
    vmax = max(tops + [n for n in needs if n] + [1]) / 3600_000
    vmax = math.ceil(vmax * 1.08)
    w = canvas_w(len(days))
    xs, bw = _xs(len(days), w)
    plot_h = H - PAD_T - PAD_B

    body = [_band(days, xs, bw), _grid(vmax, 4, "h", w)]
    for i, d in enumerate(days):
        st = d_stage(d)
        if not st:
            continue          # 缺失的天留空隙，不画成 0
        y = PAD_T + plot_h
        segs = [(st.get("total_slow_wave_sleep_time_milli", 0), DEEP),
                (st.get("total_rem_sleep_time_milli", 0), REM),
                (st.get("total_light_sleep_time_milli", 0), LIGHT),
                (st.get("total_awake_time_milli", 0), AWAKE)]
        for ms, color in segs:
            if ms <= 0:
                continue
            h = plot_h * safe_div(ms / 3600_000, vmax)
            y -= h
            body.append(f'<rect x="{xs[i] - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                        f'height="{h:.1f}" fill="{color}"/>')
    for i, d in enumerate(days):
        nd = needs[i]
        if not nd:
            continue
        y = PAD_T + plot_h * (1 - safe_div(nd / 3600_000, vmax))
        body.append(f'<line x1="{xs[i] - bw / 2 - 2:.1f}" y1="{y:.1f}" '
                    f'x2="{xs[i] + bw / 2 + 2:.1f}" y2="{y:.1f}" stroke="{FG}" '
                    f'stroke-width="1.5" stroke-dasharray="3 2" opacity="0.75"/>')
    last = max((i for i, d in enumerate(days) if d_stage(d)), default=None)
    if last is not None:
        body.append(_vlabel(xs[last],
                            PAD_T + plot_h * (1 - safe_div(tops[last] / 3600_000, vmax)) - 7,
                            fmt_dur(tops[last]), FG))
    body.append(_xlabels(days, xs))
    return svg_open("".join(body), w) + "</svg></div>"


def svg_bars(days, val_fn, color_fn, hint, vmax=None, unit="", digits=0):
    vals = [val_fn(d) for d in days]
    if not any(v is not None for v in vals):
        return empty_hint(hint)
    top = vmax or math.ceil(max([v for v in vals if v is not None] + [1]) * 1.12)
    w = canvas_w(len(days))
    xs, bw = _xs(len(days), w)
    plot_h = H - PAD_T - PAD_B
    body = [_band(days, xs, bw), _grid(top, 4, unit, w)]
    for i, v in enumerate(vals):
        if v is None:
            continue
        h = max(1.0, plot_h * safe_div(v, top))
        y = PAD_T + plot_h - h
        body.append(f'<rect x="{xs[i] - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                    f'height="{h:.1f}" fill="{color_fn(days[i], v)}" rx="1.5"/>'
                    + _sheen(xs[i] - bw / 2, y, bw, h))
    last = max((i for i, v in enumerate(vals) if v is not None), default=None)
    if last is not None:
        v = vals[last]
        body.append(_vlabel(xs[last], PAD_T + plot_h - plot_h * safe_div(v, top) - 7,
                            f"{v:.{digits}f}{unit}", color_fn(days[last], v)))
    body.append(_xlabels(days, xs))
    return svg_open("".join(body), w) + "</svg></div>"


def svg_line(days, series, hint, vmin=None, vmax=None, unit=""):
    """series: [(取值函数, 颜色, 图例名)]，多条同图。"""
    all_v = [f(d) for f, _, _ in series for d in days]
    all_v = [v for v in all_v if v is not None]
    if not all_v:
        return empty_hint(hint)
    lo = vmin if vmin is not None else min(all_v) * 0.92
    hi = vmax if vmax is not None else max(all_v) * 1.08
    w = canvas_w(len(days))
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    xs, _ = _xs(len(days), w)
    plot_h = H - PAD_T - PAD_B

    body = []
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = PAD_T + plot_h * (1 - i / 4)
        body.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{w - PAD_R}" y2="{y:.1f}" '
                    f'stroke="{LINE}" stroke-width="1"/>')
        body.append(f'<text x="{w - PAD_R + 7}" y="{y + 4:.1f}" text-anchor="start" class="ax">'
                    f'{v:.0f}{unit}</text>')

    for fn, color, _name in series:
        pts, seg = [], []
        for i, d in enumerate(days):
            v = fn(d)
            if v is None:
                if len(seg) > 1:
                    pts.append(seg)
                seg = []
                continue
            y = PAD_T + plot_h * (1 - safe_div(v - lo, hi - lo))
            seg.append((xs[i], y))
        if len(seg) > 1:
            pts.append(seg)
        for s in pts:
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in s)
            body.append(f'<polyline points="{path}" fill="none" stroke="{color}" '
                        f'stroke-width="2" stroke-linejoin="round"/>')
        for s in pts:
            for x, y in s:
                body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{color}"/>')
        # 单点也要能看见
        for i, d in enumerate(days):
            v = fn(d)
            if v is None:
                continue
            prev_ok = i > 0 and fn(days[i - 1]) is not None
            next_ok = i < len(days) - 1 and fn(days[i + 1]) is not None
            if not prev_ok and not next_ok:
                y = PAD_T + plot_h * (1 - safe_div(v - lo, hi - lo))
                body.append(f'<circle cx="{xs[i]:.1f}" cy="{y:.1f}" r="3.2" fill="{color}"/>')
    body.append(_xlabels(days, xs))
    return svg_open("".join(body), w) + "</svg></div>"


def svg_span(days, hint):
    """作息区间条。Y 轴 18:00 -> 次日 12:00，避免跨午夜被劈成两截。"""
    items = []
    for d in days:
        sl = d.get("sleep")
        if not sl:
            continue
        off = sl.get("timezone_offset", "+08:00")
        b, e = parse_ts(sl.get("start"), off), parse_ts(sl.get("end"), off)
        if b and e:
            items.append((d, b, e))
    if not items:
        return empty_hint(hint)

    def hpos(dt):
        """18:00 记为 0，次日 12:00 记为 18。"""
        h = dt.hour + dt.minute / 60
        return h - 18 if h >= 18 else h + 6

    w = canvas_w(len(days))
    xs, bw = _xs(len(days), w)
    plot_h = H - PAD_T - PAD_B
    body = []
    for i in range(7):
        hh = i * 3
        y = PAD_T + plot_h * safe_div(hh, 18)
        label = f"{(18 + hh) % 24:02d}:00"
        body.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{w - PAD_R}" y2="{y:.1f}" '
                    f'stroke="{LINE}" stroke-width="1"/>')
        body.append(f'<text x="{w - PAD_R + 7}" y="{y + 4:.1f}" text-anchor="start" class="ax">{label}</text>')

    idx = {d["date"]: i for i, d in enumerate(days)}
    for d, b, e in items:
        i = idx[d["date"]]
        y0 = max(0.0, min(18.0, hpos(b)))
        y1 = max(0.0, min(18.0, hpos(e)))
        if y1 <= y0:
            y1 = min(18.0, y0 + 0.2)
        top = PAD_T + plot_h * safe_div(y0, 18)
        h = plot_h * safe_div(y1 - y0, 18)
        body.append(f'<rect x="{xs[i] - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                    f'height="{max(2.0, h):.1f}" fill="{DEEP}" opacity="0.85" rx="2"/>')
    body.append(_xlabels(days, xs))
    return svg_open("".join(body), w) + "</svg></div>"


def svg_scatter(pairs, xlab, ylab, color=BLUE):
    pts = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pts) < 3:
        return None
    w, h = 280, 190
    pl, pb, pt = 38, 26, 10
    xsv = [p[0] for p in pts]
    ysv = [p[1] for p in pts]
    x0, x1 = min(xsv), max(xsv)
    y0, y1 = min(ysv), max(ysv)
    if x1 - x0 < 1e-9:
        x0, x1 = x0 - 1, x1 + 1
    if y1 - y0 < 1e-9:
        y0, y1 = y0 - 1, y1 + 1
    x0, x1 = x0 - (x1 - x0) * 0.08, x1 + (x1 - x0) * 0.08
    y0, y1 = y0 - (y1 - y0) * 0.08, y1 + (y1 - y0) * 0.08

    def px(v):
        return pl + (w - pl - 10) * safe_div(v - x0, x1 - x0)

    def py(v):
        return pt + (h - pt - pb) * (1 - safe_div(v - y0, y1 - y0))

    body = []
    for i in range(4):
        y = pt + (h - pt - pb) * i / 3
        body.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{w - 10}" y2="{y:.1f}" stroke="{LINE}"/>')
    n = len(pts)
    mx, my = sum(xsv) / n, sum(ysv) / n
    sxx = sum((x - mx) ** 2 for x in xsv)
    if sxx > 1e-9:
        slope = sum((xsv[i] - mx) * (ysv[i] - my) for i in range(n)) / sxx
        b = my - slope * mx
        body.append(f'<line x1="{px(x0):.1f}" y1="{py(slope * x0 + b):.1f}" '
                    f'x2="{px(x1):.1f}" y2="{py(slope * x1 + b):.1f}" '
                    f'stroke="{FG}" stroke-width="1.5" stroke-dasharray="4 3" opacity="0.6"/>')
    for x, y in pts:
        body.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3.6" fill="{color}" opacity="0.85"/>')
    body.append(f'<text x="{w / 2:.0f}" y="{h - 6}" text-anchor="middle" class="ax">{esc(xlab)}</text>')
    body.append(f'<text x="10" y="{pt + 6}" class="ax">{esc(ylab)}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" class="chart sc">'
            + "".join(body) + "</svg>")


def svg_ring(pct, color, label, text=None):
    if pct is None:
        return (f'<svg viewBox="0 0 120 120" class="ring"><circle cx="60" cy="60" r="50" '
                f'fill="none" stroke="{LINE}" stroke-width="11"/>'
                f'<text x="60" y="66" text-anchor="middle" class="ringnum" fill="{DIM}">—</text></svg>')
    r = 50
    circ = 2 * math.pi * r
    on = circ * max(0.0, min(1.0, pct / 100))
    return (f'<svg viewBox="0 0 120 120" class="ring">'
            f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="{LINE}" stroke-width="11"/>'
            f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="{color}" stroke-width="11" '
            f'stroke-linecap="round" class="arc" style="--circ:{circ:.1f}px" '
            f'stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ - on:.1f}" '
            f'transform="rotate(-90 60 60)"/>'
            f'<text x="60" y="68" text-anchor="middle" class="ringnum">'
            f'{text if text is not None else round(pct)}</text></svg>')


# ---------- 板块 ----------

def card(title, sub, body, extra=""):
    s = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    return (f'<section class="card"><h3>{esc(title)}</h3>{s}{body}{extra}</section>')


def exp(body, label="这个数字什么意思？"):
    """折叠式解读。用原生 <details>，点一下就地展开，仍然不需要一行 JavaScript。"""
    if not body:
        return ""
    return f'<details class="exp"><summary>{label}</summary><div class="expc">{body}</div></details>'


def exps(*parts):
    """把多段解读拼起来，没有数据的那几段自动略过。"""
    return "".join(x for x in parts if x)


def legend(items):
    sp = "".join(f'<span class="lg"><i style="background:{c}"></i>{esc(n)}</span>' for n, c in items)
    return f'<div class="legends">{sp}</div>'


STAT_ICONS = {
    "moon": '<path d="M20.5 13.4A8.5 8.5 0 1 1 10.6 3.5a6.7 6.7 0 0 0 9.9 9.9z"/>',
    "wave": '<path d="M3 12h3.4l2.1-5.8 3.5 11.6 2.4-7 1.6 3.2H21"/>',
    "heart": '<path d="M12 20.3 4.6 13a4.7 4.7 0 0 1 6.6-6.7l.8.8.8-.8A4.7 4.7 0 0 1 19.4 13z"/>',
    "drop": '<path d="M12 3.6c3.1 3.3 5.3 6 5.3 8.7a5.3 5.3 0 1 1-10.6 0c0-2.7 2.2-5.4 5.3-8.7z"/>',
    "temp": '<path d="M14 14.6V5a2 2 0 1 0-4 0v9.6a3.8 3.8 0 1 0 4 0z"/>',
    "bed": '<path d="M3 18v-5h18v5M3 13V8M21 18v-4M3 13h18M6.5 13v-2.5h4V13"/>',
    "cycle": '<path d="M4 12a8 8 0 0 1 13.7-5.6M20 12a8 8 0 0 1-13.7 5.6"/>'
             '<path d="M17.5 3v3.5H14M6.5 21v-3.5H10"/>',
    "bell": '<path d="M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6z"/><path d="M10.5 20a2 2 0 0 0 3 0"/>',
    "lung": '<path d="M12 4v8M8.6 12c0 4-1.4 6-2.9 6s-2-1.5-2-4 1-5.9 2.5-7.4S8.6 9 8.6 12z"/>'
            '<path d="M15.4 12c0 4 1.4 6 2.9 6s2-1.5 2-4-1-5.9-2.5-7.4S15.4 9 15.4 12z"/>',
}


def stats(rows):
    """rows: [(标签, 值)] 或 [(标签, 值, 颜色)]；颜色 None 表示用默认前景色。"""
    cells = []
    for r in rows:
        k, v, c = r[0], r[1], (r[2] if len(r) > 2 else None)
        ic = r[3] if len(r) > 3 else None
        style = f' style="color:{c}"' if c else ""
        icon = (f'<svg viewBox="0 0 24 24" class="sti">{STAT_ICONS[ic]}</svg>'
                if ic in STAT_ICONS else "")
        cells.append(f'<div class="st">{icon}<div><b{style}>{v}</b>'
                     f'<span>{esc(k)}</span></div></div>')
    return f'<div class="stats">{"".join(cells)}</div>'


def recent(days, fn, n=7):
    """最近 n 天里该指标的有效值。"""
    return [v for v in (fn(d) for d in days[-n:]) if v is not None]


def delta_txt(cur, base, digits=0, unit=""):
    if cur is None or base is None:
        return ""
    d = cur - base
    if abs(d) < (0.5 if digits == 0 else 0.05):
        return ' <span class="dim">持平</span>'
    arrow = "↑" if d > 0 else "↓"
    return f' <span class="dim">{arrow}{abs(d):.{digits}f}{unit}</span>'


# ---------- 周期聚合 ----------

def week_start(dt):
    """周一为一周开始。"""
    return dt - timedelta(days=dt.weekday())


def group_weeks(days):
    out = {}
    for d in days:
        k = week_start(d["dt"]).strftime("%Y-%m-%d")
        out.setdefault(k, []).append(d)
    return [(k, v) for k, v in sorted(out.items())]


def group_months(days):
    out = {}
    for d in days:
        k = d["dt"].strftime("%Y-%m")
        out.setdefault(k, []).append(d)
    return [(k, v) for k, v in sorted(out.items())]


def period_stats(group):
    """一个周期（周或月）的聚合指标。取不到的一律 None，不用 0 顶替。"""
    recs = [d_rec(d) for d in group]
    strains = [d_strain_done(d) for d in group]
    sleeps = [d_sleep_total(d) for d in group]
    needs = [d_sleep_need(d) for d in group]
    debts = [(n - t) for n, t in zip(needs, sleeps) if n is not None and t is not None]
    return {
        "days": len(group),
        "rec": avg(recs),
        "rec_min": min([r for r in recs if r is not None], default=None),
        "rec_max": max([r for r in recs if r is not None], default=None),
        "green": sum(1 for r in recs if r is not None and r >= 67),
        "red": sum(1 for r in recs if r is not None and r <= 33),
        "strain": avg(strains),
        "strain_sum": sum(v for v in strains if v is not None) or None,
        "sleep": avg(sleeps),
        "sleep_nights": sum(1 for x in sleeps if x is not None),
        "debt": sum(d for d in debts if d > 0) or None,
        "hrv": avg([(d_score(d, "recovery") or {}).get("hrv_rmssd_milli") for d in group]),
        "rhr": avg([(d_score(d, "recovery") or {}).get("resting_heart_rate") for d in group]),
        "perf": avg([(d_score(d, "sleep") or {}).get("sleep_performance_percentage") for d in group]),
        "deep": avg([(d_stage(d) or {}).get("total_slow_wave_sleep_time_milli") for d in group]),
        "rem": avg([(d_stage(d) or {}).get("total_rem_sleep_time_milli") for d in group]),
        "workouts": sum(len(d["workouts"]) for d in group),
        "kcal": sum((d_score(d, "cycle") or {}).get("kilojoule", 0) or 0 for d in group) / 4.184 or None,
    }


def cmp_cell(label, cur, prev, digits=0, unit="", better="high"):
    """一个带环比的指标格。better 决定升降算好还是坏。"""
    if cur is None:
        return f'<div class="st"><b>—</b><span>{esc(label)}</span></div>'
    tail = ""
    if prev is not None and abs(prev) > 1e-9:
        diff = cur - prev
        if abs(diff) >= (0.5 if digits == 0 else 0.05):
            up = diff > 0
            good = (up and better == "high") or ((not up) and better == "low")
            color = GREEN if good else RED
            tail = (f' <span style="color:{color};font-size:12px">'
                    f'{"↑" if up else "↓"}{abs(diff):.{digits}f}{unit}</span>')
        else:
            tail = ' <span class="dim">持平</span>'
    return (f'<div class="st"><b>{num(cur, digits, unit)}{tail}</b>'
            f'<span>{esc(label)}</span></div>')


def weekday_profile(days):
    """周一到周日各自的平均表现——看出哪天最容易睡不好。"""
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    out = []
    for i in range(7):
        sub = [d for d in days if d["dt"].weekday() == i]
        out.append({
            "name": names[i],
            "n": len(sub),
            "sleep": avg([d_sleep_total(d) for d in sub]),
            "rec": avg([d_rec(d) for d in sub]),
            "strain": avg([d_strain_done(d) for d in sub]),
        })
    return out


def svg_period_bars(labels, values, color_fn, unit="", hint=""):
    """周期柱状图，X 轴是周期标签而不是日期。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return empty_hint(hint)
    top = math.ceil(max(vals) * 1.15) or 1
    n = len(values)
    w = canvas_w(n)
    span = w - PAD_L - PAD_R
    step = span / n
    bw = min(46.0, max(6.0, step * 0.6))
    xs = [PAD_L + step * (i + 0.5) for i in range(n)]
    plot_h = H - PAD_T - PAD_B
    band_w = max(bw * 2.0, 24)
    body = [f'<rect x="{xs[-1] - band_w / 2:.1f}" y="{PAD_T - 5:.1f}" width="{band_w:.1f}" '
            f'height="{H - PAD_T - 1:.1f}" rx="5" fill="{FG}" opacity="0.06"/>',
            _grid(top, 4, unit)]
    for i, v in enumerate(values):
        if v is None:
            continue
        h = max(1.0, plot_h * safe_div(v, top))
        y = PAD_T + plot_h - h
        body.append(f'<rect x="{xs[i] - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                    f'height="{h:.1f}" fill="{color_fn(i, v)}" rx="2"/>'
                    + _sheen(xs[i] - bw / 2, y, bw, h))
    last = max((i for i, v in enumerate(values) if v is not None), default=None)
    if last is not None:
        body.append(_vlabel(xs[last],
                            PAD_T + plot_h - plot_h * safe_div(values[last], top) - 7,
                            f"{values[last]:.0f}{unit}", color_fn(last, values[last])))
    label_step = max(1, round(n / 8))
    for i in sorted(range(n - 1, -1, -label_step)):
        cls = "ax today" if i == n - 1 else "ax"
        body.append(f'<text x="{xs[i]:.1f}" y="{H - 11}" text-anchor="middle" class="{cls}">'
                    f'{esc(labels[i])}</text>')
    return svg_open("".join(body), w) + "</svg></div>"


# ---------- 面板一：首页（今日） ----------

def today_advice(days):
    """与每日推送同一套规则，避免两处结论打架。"""
    if not days:
        return []
    last = days[-1]
    rs = d_score(last, "recovery") or {}
    r = rs.get("recovery_score")
    lines = []
    if r is not None:
        if r >= 67:
            lines.append(("绿区", "恢复分处于绿区，可安排一次中高强度训练，目标 Strain 14～18。", GREEN))
        elif r >= 34:
            lines.append(("黄区", "恢复分处于黄区，适合中等强度，目标 Strain 8～13，避免连续两天高负荷。", YELLOW))
        else:
            lines.append(("红区", "恢复分处于红区，今天以恢复为主（散步、拉伸），目标 Strain 低于 8。", RED))
    debt = d_sleep_debt(last)
    if debt and debt > 3600_000:
        lines.append(("睡眠债", f"累积睡眠债 {fmt_dur(debt)} 尚未还清，今晚尽量比平时早睡 30 分钟。", BLUE))
    rhr = rs.get("resting_heart_rate")
    rhr_base = avg(recent(days, lambda d: (d_score(d, "recovery") or {}).get("resting_heart_rate")))
    if rhr is not None and rhr_base and rhr > rhr_base + 5:
        lines.append(("留意", "静息心率高于近期基线，留意是否有感染或过度疲劳。", RED))
    if rs.get("user_calibrating"):
        lines.append(("校准中", "手环仍在校准期，恢复分暂供参考。", DIM))
    return lines


def sec_home(days, raw):
    if not days:
        return card("今天", "", empty_hint(
            "还没有任何数据。戴上手环、手机装好快捷指令后，这里会显示当天的完整状态。"))

    last = days[-1]
    out = []

    # 今日建议
    tips = today_advice(days)
    if tips:
        items = "".join(
            f'<div class="tip"><i style="background:{c}"></i>'
            f'<b>{esc(tag)}</b><span>{esc(text)}</span></div>'
            for tag, text, c in tips)
        out.append(f'<section class="card"><h3>今日建议</h3><div class="tips">{items}</div></section>')

    # 今日睡眠明细
    st = d_stage(last)
    ss = d_score(last, "sleep") or {}
    if st:
        total = d_sleep_total(last)
        segs = [("深睡", st.get("total_slow_wave_sleep_time_milli", 0), DEEP),
                ("快速眼动", st.get("total_rem_sleep_time_milli", 0), REM),
                ("浅睡", st.get("total_light_sleep_time_milli", 0), LIGHT),
                ("清醒", st.get("total_awake_time_milli", 0), AWAKE)]
        whole = sum(v for _, v, _ in segs) or 1
        bar = "".join(f'<div class="seg" style="width:{v / whole * 100:.2f}%;background:{c}" '
                      f'title="{esc(n)} {fmt_dur(v)}"></div>' for n, v, c in segs if v > 0)
        lg = legend([(f"{n} {fmt_dur(v)}", c) for n, v, c in segs if v > 0])
        out.append(card("昨夜睡眠", f'共 {fmt_dur(total)}，各阶段占比如下',
                        f'<div class="hbar tall">{bar}</div>' + lg,
                        stats([("睡眠表现", num(ss.get("sleep_performance_percentage"), 0, "%"),
                                perf_color(ss.get("sleep_performance_percentage")), "moon"),
                               ("睡眠效率", num(ss.get("sleep_efficiency_percentage"), 0, "%"),
                                eff_color(ss.get("sleep_efficiency_percentage")), "bed"),
                               ("睡眠周期", num(st.get("sleep_cycle_count"), 0) + " 个",
                                None, "cycle"),
                               ("夜间干扰", num(st.get("disturbance_count"), 0) + " 次",
                                None, "bell")])
                        + exp(exps(
                            explain.stages(st.get("total_slow_wave_sleep_time_milli"),
                                           st.get("total_rem_sleep_time_milli"),
                                           st.get("total_light_sleep_time_milli"),
                                           st.get("total_awake_time_milli")),
                            explain.sleep_eff(ss.get("sleep_efficiency_percentage"))),
                              "这些数字什么意思？")))
    else:
        out.append(card("昨夜睡眠", "", empty_hint("昨晚没有睡眠记录，可能是没戴着手环睡。")))

    # 今日生理指标
    rs = d_score(last, "recovery") or {}
    if rs:
        f_hrv = lambda d: (d_score(d, "recovery") or {}).get("hrv_rmssd_milli")
        f_rhr = lambda d: (d_score(d, "recovery") or {}).get("resting_heart_rate")
        f_temp = lambda d: (d_score(d, "recovery") or {}).get("skin_temp_celsius")
        hrv_v, rhr_v = rs.get("hrv_rmssd_milli"), rs.get("resting_heart_rate")
        spo_v, tmp_v = rs.get("spo2_percentage"), rs.get("skin_temp_celsius")
        hrv_b, rhr_b, tmp_b = (avg(recent(days, f_hrv)), avg(recent(days, f_rhr)),
                               avg(recent(days, f_temp)))
        out.append(card("今日生理指标", "括注为与近 7 日均值的差；只有偏离到该留意时才变色",
                        stats([("心率变异性 HRV",
                                num(hrv_v, 0, "ms") + delta_txt(hrv_v, hrv_b),
                                dev_color(hrv_v, hrv_b, 0.10, worse="low"), "wave"),
                               ("静息心率",
                                num(rhr_v, 0, " bpm") + delta_txt(rhr_v, rhr_b),
                                dev_color(rhr_v, rhr_b, 0.05), "heart"),
                               ("血氧饱和度", num(spo_v, 1, "%"), spo2_color(spo_v), "drop"),
                               ("皮肤温度", num(tmp_v, 1, "°C"),
                                temp_color(tmp_v, tmp_b), "temp")]),
                        exp(exps(
                            explain.hrv(rs.get("hrv_rmssd_milli"), avg(recent(days, f_hrv))),
                            explain.rhr(rs.get("resting_heart_rate"), avg(recent(days, f_rhr))),
                            explain.spo2(rs.get("spo2_percentage")),
                            explain.skin_temp(rs.get("skin_temp_celsius"),
                                              avg(recent(days, f_temp)))),
                            "这些数字什么意思？")))

    # 今日压力（推算）
    if rs:
        st_v = stress_proxy(hrv_v, hrv_b, rhr_v, rhr_b)
        out.append(card(
            "今日压力", "接口不提供 WHOOP 的压力分，这是用 HRV 与静息心率推算的代理值",
            f'<div class="gauge">'
            f'{svg_ring(None if st_v is None else st_v / 3 * 100, stress_color(st_v), "压力", num(st_v, 1))}'
            f'<div class="gtxt"><b style="color:{stress_color(st_v)}">{stress_zone(st_v)}压力</b>'
            f'<span>满分 3.0 · 本站推算，与 App 数值不一致</span></div></div>',
            exp(explain.stress(st_v, hrv_v, hrv_b, rhr_v, rhr_b))))

    # 今日活动
    cs = d_score(last, "cycle") or {}
    act = []
    if cs:
        act += [("今日 Strain", num(cs.get("strain"), 1)),
                ("消耗", num(safe_div(cs.get("kilojoule", 0), 4.184), 0, " kcal"))]
    if act:
        out.append(card("今日活动", "手环记录的当日负荷", stats(act),
                        exp(explain.strain(cs.get("strain")))))

    # 最近 7 天速览
    week = days[-7:]
    if len(week) > 1:
        out.append(card("最近 7 天", "恢复分与睡眠时长的走势",
                        svg_bars(week, d_rec, lambda d, v: rec_color(v),
                                 "最近 7 天还没有恢复分。", vmax=100, unit="%")
                        + svg_stack(week, "最近 7 天还没有睡眠记录。"),
                        legend([("恢复分", GREEN), ("深睡", DEEP), ("快速眼动", REM),
                                ("浅睡", LIGHT), ("清醒", AWAKE)])))
    return "".join(out)


# ---------- 面板三：周统计 ----------

def sec_week(days):
    weeks = group_weeks(days)
    if not weeks:
        return card("周统计", "", empty_hint("还没有数据。累计记录一周后，这里会出现按周聚合的对比。"))

    out = []
    cur_k, cur_g = weeks[-1]
    prev = period_stats(weeks[-2][1]) if len(weeks) > 1 else {}
    c = period_stats(cur_g)
    cur_label = datetime.strptime(cur_k, "%Y-%m-%d").strftime("%m月%d日")

    out.append(card(
        f"本周（{cur_label} 起）", "对比上一周；箭头颜色表示变化是好是坏",
        '<div class="stats">'
        + cmp_cell("平均恢复分", c["rec"], prev.get("rec"), 0, "%", "high")
        + cmp_cell("平均睡眠", safe_div(c["sleep"] or 0, 3600_000) or None,
                   safe_div(prev.get("sleep") or 0, 3600_000) or None, 1, "h", "high")
        + cmp_cell("平均 Strain", c["strain"], prev.get("strain"), 1, "", "high")
        + cmp_cell("平均 HRV", c["hrv"], prev.get("hrv"), 0, "ms", "high")
        + cmp_cell("平均静息心率", c["rhr"], prev.get("rhr"), 0, "", "low")
        + cmp_cell("睡眠表现", c["perf"], prev.get("perf"), 0, "%", "high")
        + cmp_cell("累计睡眠缺口", safe_div(c["debt"] or 0, 3600_000) or None,
                   safe_div(prev.get("debt") or 0, 3600_000) or None, 1, "h", "low")
        + '</div>'
        + f'<div class="sub">本周记录 {c["days"]} 天，睡眠 {c["sleep_nights"]} 夜，'
          f'运动 {c["workouts"]} 次，绿区 {c["green"]} 天、红区 {c["red"]} 天。</div>'))

    # 逐周趋势
    labels = [datetime.strptime(k, "%Y-%m-%d").strftime("%m/%d") for k, _ in weeks]
    ws = [period_stats(g) for _, g in weeks]
    out.append(card(
        "逐周平均恢复分", "每根柱子是一整周的平均值，颜色按恢复分区间",
        svg_period_bars(labels, [w["rec"] for w in ws],
                        lambda i, v: rec_color(v), "%", "还没有满一周的恢复数据。"),
        stats([("最好的一周", max([f'{w["rec"]:.0f}%' for w in ws if w["rec"]], default="—")),
               ("周数", str(len(weeks))),
               ("最近一周", num(c["rec"], 0, "%")),
               ("整体平均", num(avg([w["rec"] for w in ws]), 0, "%"))])))

    out.append(card(
        "逐周平均睡眠", "单位小时",
        svg_period_bars(labels, [safe_div(w["sleep"] or 0, 3600_000) or None for w in ws],
                        lambda i, v: GREEN if v >= 7 else (YELLOW if v >= 6 else RED),
                        "h", "还没有满一周的睡眠数据。"),
        legend([("≥7 小时", GREEN), ("6–7 小时", YELLOW), ("<6 小时", RED)])))

    # 星期几画像
    prof = weekday_profile(days)
    if any(p["n"] for p in prof):
        rows = "".join(
            f'<tr><td>{p["name"]}</td><td>{p["n"]}</td>'
            f'<td>{fmt_dur(p["sleep"])}</td>'
            f'<td style="color:{rec_color(p["rec"])}">{num(p["rec"], 0, "%")}</td>'
            f'<td>{num(p["strain"], 1)}</td></tr>' for p in prof)
        best = max((p for p in prof if p["sleep"]), key=lambda x: x["sleep"], default=None)
        worst = min((p for p in prof if p["sleep"]), key=lambda x: x["sleep"], default=None)
        note = ""
        if best and worst and best["name"] != worst["name"]:
            note = (f'<div class="sub">{best["name"]}睡得最多（{fmt_dur(best["sleep"])}），'
                    f'{worst["name"]}最少（{fmt_dur(worst["sleep"])}），'
                    f'相差 {fmt_dur(best["sleep"] - worst["sleep"])}。</div>')
        out.append(card(
            "星期几画像", "把所有数据按星期几归类，看出哪天最容易睡不好",
            '<div class="scroll"><table><thead><tr><th>星期</th><th>天数</th><th>平均睡眠</th>'
            '<th>平均恢复</th><th>平均 Strain</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>' + note))
    return "".join(out)


# ---------- 面板四：月统计 ----------

def sec_month(days):
    months = group_months(days)
    if not months:
        return card("月统计", "", empty_hint("还没有数据。累计记录一个月后，这里会出现按月聚合的对比。"))

    out = []
    cur_k, cur_g = months[-1]
    prev = period_stats(months[-2][1]) if len(months) > 1 else {}
    c = period_stats(cur_g)
    y, m = cur_k.split("-")

    out.append(card(
        f"{y} 年 {int(m)} 月", "对比上一个月",
        '<div class="stats">'
        + cmp_cell("平均恢复分", c["rec"], prev.get("rec"), 0, "%", "high")
        + cmp_cell("平均睡眠", safe_div(c["sleep"] or 0, 3600_000) or None,
                   safe_div(prev.get("sleep") or 0, 3600_000) or None, 1, "h", "high")
        + cmp_cell("平均 HRV", c["hrv"], prev.get("hrv"), 0, "ms", "high")
        + cmp_cell("平均静息心率", c["rhr"], prev.get("rhr"), 0, "", "low")
        + cmp_cell("平均 Strain", c["strain"], prev.get("strain"), 1, "", "high")
        + cmp_cell("运动次数", c["workouts"] or None, prev.get("workouts"), 0, " 次", "high")
        + cmp_cell("总消耗", c["kcal"], prev.get("kcal"), 0, " kcal", "high")
        + '</div>'
        + f'<div class="sub">本月记录 {c["days"]} 天，睡眠 {c["sleep_nights"]} 夜；'
          f'恢复分最高 {num(c["rec_max"], 0, "%")}、最低 {num(c["rec_min"], 0, "%")}；'
          f'绿区 {c["green"]} 天、红区 {c["red"]} 天。</div>'))

    labels = [f"{int(k.split('-')[1])}月" for k, _ in months]
    ms = [period_stats(g) for _, g in months]

    out.append(card(
        "逐月平均恢复分", "每根柱子是一整月的平均值",
        svg_period_bars(labels, [x["rec"] for x in ms],
                        lambda i, v: rec_color(v), "%", "还没有满一个月的数据。")))

    out.append(card(
        "逐月平均睡眠与 HRV", "长期趋势比单日波动更能说明问题",
        svg_period_bars(labels, [safe_div(x["sleep"] or 0, 3600_000) or None for x in ms],
                        lambda i, v: GREEN if v >= 7 else (YELLOW if v >= 6 else RED),
                        "h", "还没有睡眠数据。")
        + svg_period_bars(labels, [x["hrv"] for x in ms], lambda i, v: BLUE, "ms",
                          "还没有 HRV 数据。"),
        legend([("平均睡眠（小时）", GREEN), ("平均 HRV（ms）", BLUE)])))

    best_day = max((d for d in days if d_rec(d) is not None), key=lambda d: d_rec(d), default=None)
    worst_day = min((d for d in days if d_rec(d) is not None), key=lambda d: d_rec(d), default=None)
    if best_day and worst_day:
        def one_day(d, color, label):
            metrics = "".join(
                f'<div><b>{esc(v)}</b><span>{esc(k)}</span></div>'
                for k, v in (("恢复", num(d_rec(d), 0, "%")),
                             ("睡眠", fmt_dur(d_sleep_total(d))),
                             ("Strain", num(d_strain(d), 1))))
            return (f'<div class="bwd" style="--c:{color}">'
                    f'<div class="bwt">{esc(label)}</div>'
                    f'<div class="bwv">{esc(d["date"])}</div>'
                    f'<div class="bwm">{metrics}</div></div>')
        out.append(card(
            "最好与最差的一天", "回头看看那天做了什么",
            f'<div class="bw">{one_day(best_day, GREEN, "恢复分最高")}'
            f'{one_day(worst_day, RED, "恢复分最低")}</div>'))
    return "".join(out)


def data_updated(raw):
    """上游最后一次给出新数据的时间。

    必须跟「生成于」分开显示：点了刷新但这个时间没动，说明 WHOOP 那边还没
    算出新结果（手环->手机->云那两段不归本项目管），而不是刷新坏了。
    """
    best, best_off = "", "+08:00"
    for k in ("cycles", "recovery", "sleep", "workouts"):
        for r in raw[k]:
            u = r.get("updated_at") or ""
            if u > best:                       # 同格式 UTC ISO 串，字典序即时间序
                best, best_off = u, r.get("timezone_offset") or "+08:00"
    dt = parse_ts(best, best_off) if best else None
    return dt.strftime("%m-%d %H:%M") if dt else "—"


def sec_header(days, raw, refresh=False):
    prof, body = raw["profile"] or {}, raw["body"] or {}
    n_sleep = sum(1 for d in days if d.get("sleep"))
    n_work = sum(len(d["workouts"]) for d in days)
    last = days[-1] if days else None
    prev = days[-2] if len(days) > 1 else None

    rec = d_rec(last) if last else None
    strain = d_strain(last) if last else None
    # 昨夜睡眠：优先当天，缺了就看前一天
    sl_day = last if (last and d_sleep_total(last)) else prev
    total = d_sleep_total(sl_day) if sl_day else None
    perf = ((d_score(sl_day, "sleep") or {}).get("sleep_performance_percentage")
            if sl_day else None)

    calibrating = bool((d_score(last, "recovery") or {}).get("user_calibrating")) if last else False
    cal = ('<div class="warn">手环仍在校准期，前几天的恢复分仅供参考</div>' if calibrating else "")

    who = esc(f'{prof.get("first_name", "")} {prof.get("last_name", "")}'.strip() or "我")
    bodyline = ""
    if body:
        # 每项各自成段，换行才不会把「最大心率」劈成两半
        bodyline = "".join(f"<span>{x}</span>" for x in (
            f'身高 {num(body.get("height_meter"), 2)}m',
            f'体重 {num(body.get("weight_kilogram"), 1)}kg',
            f'最大心率 {num(body.get("max_heart_rate"))} bpm'))

    cards = f'''
    <div class="hero">
      <div class="hcard">
        {svg_ring(perf, perf_color(perf), "睡眠",
                  f"{round(perf)}%" if perf is not None else "—")}
        <div class="hlab">睡眠</div>
        <div class="hval">{fmt_dur(total) if total else "暂无记录"}</div>
        {zonebar(perf_level(perf))}
        {exp(explain.sleep_perf(perf))}
      </div>
      <div class="hcard">
        {svg_ring(rec, rec_color(rec), "恢复",
                  f"{round(rec)}%" if rec is not None else "—")}
        <div class="hlab">恢复</div>
        <div class="hval" style="color:{rec_color(rec)}">{rec_zone(rec)}</div>
        {zonebar(rec_level(rec))}
        {exp(explain.recovery(rec))}
      </div>
      <div class="hcard">
        {svg_ring(None if strain is None else strain / 21 * 100, BLUE, "负荷",
                  f"{strain:.1f}" if strain is not None else "—")}
        <div class="hlab">负荷</div>
        <div class="hval">满分 21</div>
        {zonebar(strain_level(strain), (BLUE, BLUE, BLUE))}
        {exp(explain.strain(strain))}
      </div>
    </div>'''

    # 通用版只查看后台已经生成的最新页面，不连接原实例的刷新或 AI 接口。
    btn = '<div class="acts"><a class="rfs" href="./">查看最新页面</a></div>' if refresh else ''
    return f'''<header>
      <div class="htop">
        <h1>WHOOP 健康工作台</h1>
        <div class="meta"><span>{who}</span>{bodyline}</div>
        {btn}
      </div>
      <div class="cover"><span>已记录 {len(days)} 天</span><span>睡眠 {n_sleep} 夜</span><span>运动 {n_work} 次</span><span>数据最新至 {data_updated(raw)}</span><span>上次刷新 {datetime.now(DISPLAY_TZ).strftime("%m-%d %H:%M")}</span></div>
      {cal}{cards}
    </header>'''


def sec_sleep(days):
    out = ['<h2>睡眠</h2>']
    st_now = next((d_stage(d) for d in reversed(days) if d_stage(d)), None) or {}

    # 1.1 时长与分期
    tot7 = recent(days, d_sleep_total)
    deep7 = recent(days, lambda d: (d_stage(d) or {}).get("total_slow_wave_sleep_time_milli"))
    rem7 = recent(days, lambda d: (d_stage(d) or {}).get("total_rem_sleep_time_milli"))
    cyc7 = recent(days, lambda d: (d_stage(d) or {}).get("sleep_cycle_count"))
    dis7 = recent(days, lambda d: (d_stage(d) or {}).get("disturbance_count"))
    a_tot = avg(tot7)
    bed7 = recent(days, lambda d: (d_stage(d) or {}).get("total_in_bed_time_milli"))
    nod7 = recent(days, lambda d: (d_stage(d) or {}).get("total_no_data_time_milli"))
    rows = [
        ("近 7 日平均睡眠", fmt_dur(a_tot)),
        ("平均在床时间", fmt_dur(avg(bed7)) if bed7 else "—"),
        ("深睡占比", num(safe_div(avg(deep7) or 0, a_tot or 1) * 100, 0, "%") if a_tot else "—"),
        ("快速眼动占比", num(safe_div(avg(rem7) or 0, a_tot or 1) * 100, 0, "%") if a_tot else "—"),
        ("平均睡眠周期", num(avg(cyc7), 1) + " 个" if cyc7 else "—"),
        ("平均夜间干扰", num(avg(dis7), 1) + " 次" if dis7 else "—"),
        ("手环失联时长", fmt_dur(avg(nod7)) if nod7 else "—"),
    ]
    out.append(card(
        "时长与分期", "每晚自下而上：深睡 / 快速眼动 / 浅睡 / 清醒；白色虚线是当晚的睡眠需求",
        svg_stack(days, "还没有睡眠记录。戴着手环睡一晚，第二天这里就会出现分期柱状图。"),
        legend([("深睡", DEEP), ("快速眼动", REM), ("浅睡", LIGHT), ("清醒", AWAKE)]) + stats(rows)
        + exp(explain.stages(st_now.get("total_slow_wave_sleep_time_milli"),
                             st_now.get("total_rem_sleep_time_milli"),
                             st_now.get("total_light_sleep_time_milli"),
                             st_now.get("total_awake_time_milli")))))

    # 1.2 质量评分
    f_perf = lambda d: (d_score(d, "sleep") or {}).get("sleep_performance_percentage")
    f_eff = lambda d: (d_score(d, "sleep") or {}).get("sleep_efficiency_percentage")
    f_con = d_consistency
    f_rr = lambda d: (d_score(d, "sleep") or {}).get("respiratory_rate")
    lastv = lambda f: next((f(d) for d in reversed(days) if f(d) is not None), None)
    rows = [
        ("睡眠表现", num(lastv(f_perf), 0, "%") + delta_txt(lastv(f_perf), avg(recent(days, f_perf))),
         perf_color(lastv(f_perf))),
        ("睡眠效率", num(lastv(f_eff), 0, "%") + delta_txt(lastv(f_eff), avg(recent(days, f_eff))),
         eff_color(lastv(f_eff))),
        ("睡眠规律性", num(lastv(f_con), 0, "%") + delta_txt(lastv(f_con), avg(recent(days, f_con)))),
        ("呼吸频率", num(lastv(f_rr), 1) + " 次/分" if lastv(f_rr) else "—"),
    ]
    out.append(card(
        "质量评分", "三项百分比越高越好；括注是与近 7 日均值的差",
        svg_line(days, [(f_perf, GREEN, "表现"), (f_eff, BLUE, "效率"), (f_con, YELLOW, "规律性")],
                 "还没有睡眠评分。WHOOP 需要至少一整晚的佩戴记录才能算出这三项。",
                 vmin=0, vmax=100, unit="%"),
        legend([("睡眠表现", GREEN), ("睡眠效率", BLUE), ("睡眠规律性", YELLOW)]) + stats(rows)
        + exp(exps(explain.sleep_perf(lastv(f_perf)),
                   explain.sleep_eff(lastv(f_eff))), "这些数字什么意思？")))

    # 1.3 睡眠债与需求
    out.append(sec_sleep_debt(days))

    # 1.4 作息规律
    out.append(sec_sleep_rhythm(days))
    return "".join(out)


def sec_sleep_debt(days):
    have = [d for d in days if d_sleep_total(d) and d_sleep_need(d)]
    if not have:
        return card("睡眠债与需求", "实际睡眠与身体需求的差额",
                    empty_hint("还没有足够数据计算睡眠债。WHOOP 会根据历史睡眠、当日负荷和小睡自动推算每晚需求。"))

    last = have[-1]
    sn = (d_score(last, "sleep") or {}).get("sleep_needed") or {}
    need = d_sleep_need(last)
    total = d_sleep_total(last)
    gap = (need or 0) - (total or 0)

    w = canvas_w(len(days))
    xs, bw = _xs(len(days), w)
    plot_h = H - PAD_T - PAD_B
    vmax = math.ceil(max([max(d_sleep_total(d) or 0, d_sleep_need(d) or 0)
                          for d in days] + [1]) / 3600_000 * 1.08)
    body = [_grid(vmax, 4, "h")]
    for i, d in enumerate(days):
        t, nd = d_sleep_total(d), d_sleep_need(d)
        if not t or not nd:
            continue
        hw = bw / 2
        h1 = plot_h * safe_div(t / 3600_000, vmax)
        h2 = plot_h * safe_div(nd / 3600_000, vmax)
        body.append(f'<rect x="{xs[i] - hw:.1f}" y="{PAD_T + plot_h - h1:.1f}" width="{hw * 0.86:.1f}" '
                    f'height="{max(1.0, h1):.1f}" fill="{DEEP}"/>')
        color = RED if nd > t else GREEN
        body.append(f'<rect x="{xs[i] + hw * 0.06:.1f}" y="{PAD_T + plot_h - h2:.1f}" '
                    f'width="{hw * 0.86:.1f}" height="{max(1.0, h2):.1f}" fill="none" '
                    f'stroke="{color}" stroke-width="1.6"/>')
    body.append(_xlabels(days, xs))
    chart = svg_open("".join(body), w) + "</svg></div>"

    # 需求拆解：基础 + 睡眠债 + 负荷追加 − 小睡抵扣
    parts = [("基础需求", sn.get("baseline_milli", 0), LIGHT),
             ("累积睡眠债", sn.get("need_from_sleep_debt_milli", 0), RED),
             ("近期负荷追加", sn.get("need_from_recent_strain_milli", 0), YELLOW)]
    nap_off = sn.get("need_from_recent_nap_milli", 0)
    pos = sum(v for _, v, _ in parts) or 1
    segs = []
    acc = 0.0
    for name, v, c in parts:
        if v <= 0:
            continue
        w = safe_div(v, pos) * 100
        segs.append(f'<div class="seg" style="width:{w:.2f}%;background:{c}" '
                    f'title="{esc(name)} {fmt_dur(v)}"></div>')
        acc += w
    nap_note = "　小睡已抵扣 " + fmt_dur(abs(nap_off)) if nap_off else ""
    breakdown = (
        "最近一晚需求 " + fmt_dur(need)
        + " = 基础 " + fmt_dur(sn.get("baseline_milli", 0))
        + " + 睡眠债 " + fmt_dur(sn.get("need_from_sleep_debt_milli", 0))
        + " + 负荷追加 " + fmt_dur(sn.get("need_from_recent_strain_milli", 0))
        + nap_note
    )
    bar = ('<div class="hbar">' + "".join(segs) + "</div>"
           + '<div class="sub">' + esc(breakdown) + "</div>")

    if gap > 0:
        cycles_owed = gap / 5400_000
        big = (f'<div class="debt"><b style="color:{RED}">{fmt_dur(gap)}</b>'
               f'<span>当前睡眠缺口，相当于欠了约 {cycles_owed:.1f} 个睡眠周期</span></div>')
    else:
        big = (f'<div class="debt"><b style="color:{GREEN}">已还清</b>'
               f'<span>最近一晚睡眠已覆盖身体需求，多睡了 {fmt_dur(-gap)}</span></div>')

    return card("睡眠债与需求", "实心柱 = 实际睡眠，空心框 = 当晚需求；框比柱高就是欠觉",
                chart, legend([("实际睡眠", DEEP), ("需求（已满足）", GREEN), ("需求（有缺口）", RED)])
                + big + bar + exp(explain.sleep_debt(sn.get("need_from_sleep_debt_milli", 0) / 60000)))


def sec_sleep_rhythm(days):
    items = []
    for d in days:
        sl = d.get("sleep")
        if not sl:
            continue
        off = sl.get("timezone_offset", "+08:00")
        b, e = parse_ts(sl.get("start"), off), parse_ts(sl.get("end"), off)
        if b and e:
            items.append((b, e))

    rows = []
    if items:
        def mins(dt):
            h = dt.hour + dt.minute / 60
            return (h - 24 if h >= 18 else h) * 60      # 以午夜为 0，睡前为负
        beds = [mins(b) for b, _ in items]
        ups = [e.hour * 60 + e.minute for _, e in items]
        mb = sum(beds) / len(beds)
        mu = sum(ups) / len(ups)

        def clock(m, base_mid=True):
            t = (m + 1440) % 1440 if base_mid else m % 1440
            return f"{int(t // 60):02d}:{int(round(t % 60)):02d}"

        sd = math.sqrt(sum((x - mb) ** 2 for x in beds) / len(beds)) if len(beds) > 1 else 0
        grade = "很规律" if sd <= 20 else ("较规律" if sd <= 45 else "波动较大")
        rows = [("平均上床", clock(mb)), ("平均起床", clock(mu, False)),
                ("入睡时间波动", f"±{sd:.0f} 分钟"), ("规律程度", grade)]

    return card("作息规律", "纵轴是 18:00 到次日 12:00，每根条从上床延伸到起床",
                svg_span(days, "还没有作息数据。连续记录几晚后，这里能看出你的上床时间在往哪边漂。"),
                stats(rows) if rows else "")


def sec_recovery(days):
    f_rec = d_rec
    f_hrv = lambda d: (d_score(d, "recovery") or {}).get("hrv_rmssd_milli")
    f_rhr = lambda d: (d_score(d, "recovery") or {}).get("resting_heart_rate")
    f_spo = lambda d: (d_score(d, "recovery") or {}).get("spo2_percentage")
    f_tmp = lambda d: (d_score(d, "recovery") or {}).get("skin_temp_celsius")
    lastv = lambda f: next((f(d) for d in reversed(days) if f(d) is not None), None)

    out = ['<h2>恢复与生理指标</h2>']
    out.append(card(
        "恢复分", "绿区 ≥67 可以上强度；黄区 34–66 适合中等；红区 ≤33 以恢复为主",
        svg_bars(days, f_rec, lambda d, v: rec_color(v),
                 "还没有恢复分。WHOOP 需要一整晚的睡眠数据才能算出当日恢复。", vmax=100, unit="%"),
        stats([("最新恢复分", num(lastv(f_rec), 0, "%"), rec_color(lastv(f_rec))),
               ("近 7 日平均", num(avg(recent(days, f_rec)), 0, "%")),
               ("绿区天数", str(sum(1 for d in days if (d_rec(d) or 0) >= 67))),
               ("红区天数", str(sum(1 for d in days if d_rec(d) is not None and d_rec(d) <= 33)))])
        + exp(explain.recovery(lastv(f_rec)))))

    out.append(card(
        "心率变异性 HRV 与静息心率", "HRV 越高、静息心率越低，通常代表恢复得越好",
        svg_line(days, [(f_hrv, GREEN, "HRV")],
                 "还没有 HRV 数据。", unit="ms")
        + svg_line(days, [(f_rhr, BLUE, "静息心率")],
                   "还没有静息心率数据。", unit=""),
        legend([("心率变异性 HRV（ms）", GREEN), ("静息心率（bpm）", BLUE)])
        + stats([("最新 HRV", num(lastv(f_hrv), 0, "ms") + delta_txt(lastv(f_hrv), avg(recent(days, f_hrv))),
                  dev_color(lastv(f_hrv), avg(recent(days, f_hrv)), 0.10, worse="low")),
                 ("最新静息心率",
                  num(lastv(f_rhr), 0, " bpm") + delta_txt(lastv(f_rhr), avg(recent(days, f_rhr))),
                  dev_color(lastv(f_rhr), avg(recent(days, f_rhr)), 0.05)),
                 ("HRV 近 7 日均值", num(avg(recent(days, f_hrv)), 0, "ms")),
                 ("静息心率 7 日均值", num(avg(recent(days, f_rhr)), 0, " bpm"))])
        + exp(exps(explain.hrv(lastv(f_hrv), avg(recent(days, f_hrv))),
                   explain.rhr(lastv(f_rhr), avg(recent(days, f_rhr)))),
              "这些数字什么意思？")))

    st_series = stress_series(days)
    out.append(card(
        "压力趋势（本站推算）", "由 HRV 与静息心率相对个人基线的偏离推算，量程 0–3；"
                              "不是 WHOOP App 里的压力分",
        svg_bars(days, lambda d: st_series.get(d["date"]), lambda d, v: stress_color(v),
                 "还没有足够数据推算压力：需要先积累几天的 HRV 与静息心率作基线。",
                 vmax=3, digits=1),
        legend([("低（<1）", OPT), ("中（1–2）", YELLOW), ("高（>2）", POOR)])))

    base_t = avg([f_tmp(d) for d in days])
    tnote = ""
    if base_t is not None and lastv(f_tmp) is not None:
        dev = lastv(f_tmp) - base_t
        if abs(dev) > 0.8:
            tnote = (f'<div class="warn">皮肤温度偏离个人基线 {dev:+.1f}°C，'
                     f'留意是否有感染或过度疲劳</div>')
    out.append(card(
        "血氧与皮肤温度", "仅 WHOOP 4.0 及以上型号提供；皮温看的是与个人基线的偏离，不是绝对值",
        svg_line(days, [(f_spo, GREEN, "血氧")], "还没有血氧数据。", unit="%")
        + svg_line(days, [(f_tmp, YELLOW, "皮温")], "还没有皮肤温度数据。", unit="°"),
        legend([("血氧饱和度（%）", GREEN), ("皮肤温度（°C）", YELLOW)])
        + stats([("最新血氧", num(lastv(f_spo), 1, "%"), spo2_color(lastv(f_spo))),
                 ("最新皮温", num(lastv(f_tmp), 1, "°C"), temp_color(lastv(f_tmp), base_t)),
                 ("皮温个人基线", num(base_t, 1, "°C")),
                 ("偏离基线", (f"{lastv(f_tmp) - base_t:+.1f}°C"
                            if (base_t is not None and lastv(f_tmp) is not None) else "—"))])
        + tnote
        + exp(exps(explain.spo2(lastv(f_spo)),
                   explain.skin_temp(lastv(f_tmp), base_t)), "这些数字什么意思？")))
    return "".join(out)


def sec_strain(days):
    last_strain = next((d_strain(d) for d in reversed(days) if d_strain(d) is not None), None)
    kj = [(d_score(d, "cycle") or {}).get("kilojoule") for d in days]
    kcal = sum(v / 4.184 for v in kj if v)
    f_ahr = lambda d: (d_score(d, "cycle") or {}).get("average_heart_rate")
    f_mhr = lambda d: (d_score(d, "cycle") or {}).get("max_heart_rate")

    return ('<h2>每日负荷</h2>' + card(
        "Strain 心血管负荷", "纵轴固定 0–21（WHOOP 的对数刻度），柱子颜色是当天的恢复分区间——"
                             "一眼看出绿区那天有没有练、红区那天是不是硬上了",
        svg_bars(days, d_strain, lambda d, v: rec_color(d_rec(d)) if d_rec(d) else BLUE,
                 "还没有负荷数据。", vmax=21, digits=1),
        legend([("当天恢复在绿区", GREEN), ("黄区", YELLOW), ("红区", RED), ("无恢复分", BLUE)])
        + stats([("近 7 日平均 Strain", num(avg(recent(days, d_strain_done)), 1)),
                 ("累计消耗", num(kcal, 0, " kcal") if kcal else "—"),
                 ("平均心率", num(avg(recent(days, f_ahr)), 0, " bpm")),
                 ("最高心率", num(max([v for v in (f_mhr(d) for d in days) if v] or [None]) if any(
                     f_mhr(d) for d in days) else None, 0, " bpm"))])
        + exp(explain.strain(last_strain))))


def alt_txt(sc):
    """累计爬升 / 起终点净升降，两个字段合并成一列。"""
    gain = sc.get("altitude_gain_meter")
    change = sc.get("altitude_change_meter")
    if gain is None and change is None:
        return "—"
    parts = []
    if gain is not None:
        parts.append(f"{round(gain)}m")
    if change is not None:
        parts.append(f"{change:+.0f}m")
    return " / ".join(parts)


def sec_workouts(days):
    rows = []
    for d in days:
        for w in d["workouts"]:
            sc = w.get("score") or {}
            off = w.get("timezone_offset", "+08:00")
            b, e = parse_ts(w.get("start"), off), parse_ts(w.get("end"), off)
            mins = round((e - b).total_seconds() / 60) if (b and e) else None
            zd = sc.get("zone_durations") or {}
            zvals = [zd.get(k, 0) for k in ("zone_zero_milli", "zone_one_milli", "zone_two_milli",
                                            "zone_three_milli", "zone_four_milli", "zone_five_milli")]
            ztot = sum(zvals) or 1
            zbar = "".join(f'<i style="width:{v / ztot * 100:.1f}%;background:{ZONES[i]}"></i>'
                           for i, v in enumerate(zvals) if v > 0)
            pct = sc.get("percent_recorded")
            # 文档写 0–100，实际返回 0–1 的小数
            flag = ('<span class="tag">记录不完整</span>'
                    if (pct is not None and pct < (0.9 if pct <= 1 else 90)) else "")
            sport = SPORTS.get((w.get("sport_name") or "").lower(), w.get("sport_name") or "未知")
            dist = sc.get("distance_meter")
            rows.append(
                f'<tr><td>{esc(d["date"])}<br><span class="dim">{b.strftime("%H:%M") if b else ""}</span></td>'
                f'<td>{esc(sport)}{flag}</td>'
                f'<td>{num(mins, 0, " 分钟")}</td>'
                f'<td style="color:{BLUE}">{num(sc.get("strain"), 1)}</td>'
                f'<td>{num(sc.get("average_heart_rate"), 0)} / {num(sc.get("max_heart_rate"), 0)}</td>'
                f'<td>{num(dist / 1000, 2, " km") if dist else "—"}</td>'
                f'<td>{alt_txt(sc)}</td>'
                f'<td>{num(safe_div(sc.get("kilojoule") or 0, 4.184), 0, " kcal") if sc.get("kilojoule") else "—"}</td>'
                f'<td><div class="zbar">{zbar}</div></td></tr>')

    if not rows:
        body = empty_hint("还没有运动记录。在 WHOOP App 里记录一次训练后，这里会出现明细和心率区间分布。")
    else:
        body = ('<div class="scroll"><table><thead><tr><th>日期</th><th>项目</th><th>时长</th>'
                '<th>Strain</th><th>平均/最高心率</th><th>距离</th><th>爬升/净升降</th>'
                '<th>消耗</th><th>心率区间分布</th></tr></thead>'
                '<tbody>' + "".join(reversed(rows)) + '</tbody></table></div>')

    zl = legend([(f"区间 {i}", ZONES[i]) for i in range(6)])
    return '<h2>运动记录</h2>' + card(
        "训练明细", "心率区间从左到右为 0–5 区，越靠右强度越高", body, zl if rows else "")


def sec_correlation(days):
    ok = [d for d in days if d_sleep_total(d) is not None]
    n = len(ok)
    if n < 7:
        return '<h2>关联分析</h2>' + card(
            "睡眠与恢复的关系", "把不同指标放在一起，看你身上真实存在的规律",
            empty_hint(f"样本还太少（当前 {n} 天有睡眠记录），累积到 14 天后这里会出现结论。"))

    # 睡眠时长 -> 醒来后的恢复分：同一个 cycle 里的 sleep 和 recovery 就是同一晚，不能错开一天
    p1 = [(d_sleep_total(d) / 3600_000, d_rec(d))
          for d in days if d_sleep_total(d) and d_rec(d) is not None]
    # 当日 Strain -> 当夜深睡
    p2 = [(d_strain(d), (d_stage(d) or {}).get("total_slow_wave_sleep_time_milli", 0) / 60000)
          for d in days if d_strain(d) is not None and d_stage(d)]
    # 睡眠规律性 -> 恢复分
    p3 = [(d_consistency(d), d_rec(d))
          for d in days if d_consistency(d) is not None and d_rec(d) is not None]

    blocks = []
    for pairs, xl, yl, title, unit_hi, unit_lo in (
        (p1, "睡眠时长（小时）", "次日恢复分", "睡得越久，第二天恢复越好吗", "恢复分", "睡眠时长"),
        (p2, "当日 Strain", "当夜深睡（分钟）", "练得越狠，深睡越多吗", "深睡", "Strain"),
        (p3, "睡眠规律性（%）", "恢复分", "作息越规律，恢复越好吗", "恢复分", "规律性"),
    ):
        sv = svg_scatter(pairs, xl, yl)
        r = pearson(pairs)
        if sv is None:
            blocks.append(f'<div class="corr">{empty_hint("这项的有效样本不足 3 个。")}</div>')
            continue
        if r is None:
            note = "样本数据没有波动，暂时算不出相关性。"
        else:
            note = f"r = {r:+.2f}，{r_words(r)}"
            if abs(r) >= 0.35:
                note += f"——{unit_lo}越高，{unit_hi}倾向于{'越高' if r > 0 else '越低'}"
        blocks.append(f'<div class="corr"><h4>{esc(title)}</h4>{sv}'
                      f'<div class="sub">{esc(note)}</div></div>')

    return '<h2>关联分析</h2>' + card(
        "你身上的规律", f"基于最近 {n} 天的记录；r 是皮尔逊相关系数，越接近 ±1 关系越强",
        f'<div class="corrs">{"".join(blocks)}</div>')


# ---------- 输出 ----------

CSS = """
* { box-sizing: border-box; }
body { margin:0; background:%(BG)s; color:%(FG)s;
  font: 14px/1.6 -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
  -webkit-font-smoothing: antialiased; }
.wrap { max-width: 780px; margin: 0 auto;
  padding: 26px 20px calc(88px + env(safe-area-inset-bottom)); }
h1 { font-size: 26px; margin: 0; letter-spacing: .5px; }
h2 { font-size: 18px; margin: 40px 0 14px; padding-left: 10px;
  border-left: 3px solid %(BLUE)s; }
h3 { font-size: 15px; margin: 0 0 2px; }
h4 { font-size: 13px; margin: 0 0 6px; color:%(FG)s; }
.meta, .cover { color:%(DIM)s; font-size: 12.5px; }
.htop { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
.cover { margin-top: 8px; }
/* 分隔点跟在每段末尾（最后一段除外）：放在段首的话，一换行它就孤零零
   落到行首去了。颜色也得比边框色亮，否则等于没有。 */
.cover, .meta { display:flex; flex-wrap:wrap; gap:3px 0; }
.cover span, .meta span { white-space:nowrap; }
.cover span:not(:last-child)::after, .meta span:not(:last-child)::after {
  content:"·"; margin:0 9px; color:rgba(255,255,255,.3); }
.acts { margin-left:auto; align-self:center; display:flex; gap:8px; flex-wrap:wrap; }
.rfs { color:%(BLUE)s; text-decoration:none;
  border:1px solid %(LINE)s; border-radius:999px; padding:6px 15px;
  font-size:12.5px; white-space:nowrap; }
.rfs:active { background:%(CARD)s; }
.card { background:%(CARD)s; border:1px solid %(LINE)s; border-radius:12px;
  padding:16px 18px; margin-bottom:14px; }
.sub { color:%(DIM)s; font-size:12.5px; margin-bottom:10px; }
.chartbox { overflow-x:auto; -webkit-overflow-scrolling:touch; margin:6px 0 2px; }
.chart { display:block; width:100%%; height:auto; margin:0 auto; }
.chart.sc { min-width:0; }
.ax { fill:%(DIM)s; font-size:10px; }
.ax.today { fill:%(FG)s; font-weight:600; }
.vlab { font-size:12px; font-weight:700; }
.empty { display:flex; flex-direction:column; align-items:center; gap:7px;
  color:%(DIM)s; background:rgba(255,255,255,.016);
  border:1px solid rgba(255,255,255,.05); border-radius:12px;
  padding:30px 20px; text-align:center; font-size:13px; }
.empty .eico { width:26px; height:26px; fill:none; stroke:currentColor;
  stroke-width:1.6; stroke-linecap:round; opacity:.45; }
.empty b { font-family:var(--num); font-size:11.5px; font-weight:600;
  letter-spacing:.15em; text-transform:uppercase; color:%(FG)s; opacity:.72; }
.empty span { font-size:12.5px; line-height:1.7; max-width:32em; }
.zb { display:flex; justify-content:center; gap:3px; margin-top:8px; }
.zb i { width:17px; height:4px; border-radius:2px; }
.legends { display:flex; flex-wrap:wrap; gap:14px; margin:8px 0 4px; }
.lg { color:%(DIM)s; font-size:12px; display:flex; align-items:center; gap:5px; }
.lg i { width:10px; height:10px; border-radius:2px; display:inline-block; }
.stats { display:flex; flex-wrap:wrap; gap:10px; margin-top:12px;
  border-top:1px solid %(LINE)s; padding-top:12px; }
.st { flex:1 1 120px; display:flex; gap:8px; align-items:flex-start; }
.sti { width:15px; height:15px; flex:0 0 15px; margin-top:4px; fill:none;
  stroke:%(DIM)s; stroke-width:1.6; stroke-linecap:round; stroke-linejoin:round;
  opacity:.8; }
.st b { display:block; font-size:17px; font-weight:650; letter-spacing:-.01em; }
.st span { color:%(DIM)s; font-size:12px; }
.dim { color:%(DIM)s; font-size:12px; font-weight:400; }

/* 最好与最差的一天：每天「标签 → 日期 → 三个指标」三层。
   不套 .st —— 那是「一个大数字 + 一句小标签」，把整句话塞进去会连成一串，
   小标签还会被挤成竖排。--c 是这一天的强调色，只上在日期和左侧竖条上。 */
.bw { display:grid; grid-template-columns:1fr 1fr; gap:10px 18px; margin-top:12px;
  border-top:1px solid %(LINE)s; padding-top:12px; }
.bwd { border-left:3px solid var(--c); padding-left:10px; }
.bwt { color:%(DIM)s; font-size:12px; line-height:1.4; }
.bwv { font-family:var(--num); font-size:16px; font-weight:650; color:var(--c);
  letter-spacing:-.01em; margin:2px 0 9px; font-variant-numeric:tabular-nums; }
.bwm { display:flex; flex-wrap:wrap; gap:6px 16px; }
.bwm b { display:block; font-family:var(--num); font-size:15px; font-weight:650;
  color:%(FG)s; letter-spacing:-.01em; font-variant-numeric:tabular-nums; }
.bwm span { color:%(DIM)s; font-size:11.5px; }
/* 窄屏两栏各只剩 170px，三个指标横排会折行错位，改成上下两块 */
@media (max-width:520px) { .bw { grid-template-columns:1fr; gap:14px; } }
.hero { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:18px;
  max-width:520px; margin-left:auto; margin-right:auto;
  align-items:start; }   /* start：展开解读时只撑高自己，不牵连同行的卡片 */
.exp { margin-top:10px; border-top:1px solid %(LINE)s; padding-top:9px; text-align:left; }
.exp summary { color:%(BLUE)s; font-size:12.5px; cursor:pointer; list-style:none;
  display:flex; align-items:center; gap:5px; }
.exp summary::-webkit-details-marker { display:none; }
.exp summary::before { content:"▸"; font-size:9px; }
.exp[open] summary::before { content:"▾"; }
.expc { color:%(DIM)s; font-size:12.5px; line-height:1.7; margin-top:11px; }
.expc b { color:%(FG)s; font-weight:600; }

/* 解读的骨架：指标名 → 当前值与档 → 一句话结论 → 档位条 → 逐条小标题。
   大段文字在手机上没人读得下去，条目化才能一眼找到想看的那条。
   --c 是这一段的强调色，由 .t-* 决定，值、档位标签、档位条共用。 */
.ex + .ex { margin-top:15px; padding-top:14px; border-top:1px solid %(LINE)s; }
/* 指标名要一眼和条目小标题分开：左侧一道该段的强调色 */
.ex h5 { margin:0 0 10px; font-size:13.5px; font-weight:600; color:%(FG)s;
  letter-spacing:.02em; padding-left:9px; border-left:3px solid var(--c);
  line-height:1.35; }
.exh { display:flex; align-items:baseline; gap:9px; flex-wrap:wrap; margin-bottom:8px; }
.exh b { font-family:var(--num); font-size:23px; font-weight:650; line-height:1.1;
  letter-spacing:-.02em; color:var(--c); }
.exh em { font-style:normal; font-size:11.5px; font-weight:600; color:var(--c);
  border:1px solid currentColor; border-radius:999px; padding:2px 9px; opacity:.85; }
.exl { margin:0 0 11px; color:%(FG)s; font-size:13px; line-height:1.6; }
/* 档位条：不用读数字就知道自己落在哪一档 */
.exs { display:flex; gap:4px; margin:0 0 13px; }
.exs i { flex:1 1 0; min-width:0; font-style:normal; text-align:center;
  font-size:11px; color:%(DIM)s;
  background:rgba(255,255,255,.04); border-radius:6px; padding:5px 3px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.exs i.on { color:#0A0A0A; background:var(--c); font-weight:600; }
/* 小标题与内容分两列；窄屏改成上下堆叠 */
.exd { margin:0; display:grid; grid-template-columns:max-content 1fr; gap:8px 14px; }
.exd dt { color:%(FG)s; font-size:11.5px; font-weight:600; line-height:1.6;
  letter-spacing:.03em; }
.exd dd { margin:0; color:%(DIM)s; font-size:12.5px; line-height:1.6; }
.exn { margin:12px 0 0; color:%(YELLOW)s; font-size:12px; line-height:1.6;
  background:rgba(255,222,0,.06); border-left:2px solid currentColor;
  border-radius:0 6px 6px 0; padding:7px 11px; }
.t-g { --c:%(GREEN)s; } .t-y { --c:%(YELLOW)s; } .t-o { --c:%(ORANGE)s; }
.t-r { --c:%(RED)s; } .t-b { --c:%(BLUE)s; } .t-n { --c:%(FG)s; }
@media (max-width: 520px) {
  .exd { grid-template-columns:1fr; gap:3px; }
  .exd dd { margin-bottom:10px; }
  .exd dd:last-of-type { margin-bottom:0; }
  .exs i { font-size:10px; padding:5px 2px; }
}
.hcard .exp { border-top:none; margin-top:9px; padding-top:0; }
.hcard .exp summary { justify-content:center; font-size:0; }
.hcard .exp summary::before { display:none; }
.hcard .exp summary::after {
  content:"?"; font-family:var(--num); font-size:11.5px; font-weight:600;
  width:20px; height:20px; line-height:18px; text-align:center;
  border:1px solid rgba(255,255,255,.2); border-radius:50%%; color:%(DIM)s; }
.hcard .exp[open] summary::after { content:"×"; color:%(FG)s;
  border-color:rgba(255,255,255,.4); }
/* 展开解读时这张卡占满整行：不然窄列里一行只放得下十来个字，读着费劲。
   :has() 不支持的老浏览器只是维持窄列，内容照样能看。 */
.hcard:has(.exp[open]) { grid-column: 1 / -1; }
.hcard { background:%(CARD)s; border:1px solid %(LINE)s; border-radius:12px;
  padding:16px; text-align:center; }
.hlab { color:%(FG)s; font-size:12.5px; font-weight:600; margin-top:7px;
  letter-spacing:.4px; }
.hval { color:%(DIM)s; font-size:12px; margin-top:4px; }
.big { font-size:32px; font-weight:600; line-height:1.3; }
.ring { width:96px; height:96px; }
.gauge { display:flex; align-items:center; gap:16px; }
.gtxt b { display:block; font-size:19px; font-weight:600; }
.gtxt span { color:%(DIM)s; font-size:12px; }
.ringnum { fill:%(FG)s; font-size:26px; font-weight:600; }
.warn { background:rgba(255,222,0,.09); border:1px solid rgba(255,222,0,.3);
  color:%(YELLOW)s; border-radius:8px; padding:8px 12px; font-size:12.5px; margin-top:12px; }
.debt { display:flex; align-items:baseline; gap:12px; margin:14px 0 8px; flex-wrap:wrap; }
.debt b { font-size:26px; }
.debt span { color:%(DIM)s; font-size:12.5px; }
.hbar { display:flex; height:14px; border-radius:7px; overflow:hidden;
  background:%(LINE)s; margin-bottom:6px; }
.hbar .seg { height:100%%; }
.scroll { overflow-x:auto; -webkit-overflow-scrolling:touch; }
table { width:100%%; min-width:620px; border-collapse:collapse; font-size:13px; }
th { text-align:left; color:%(DIM)s; font-weight:500; font-size:12px;
  border-bottom:1px solid %(LINE)s; padding:8px 6px; }
td { padding:10px 6px; border-bottom:1px solid %(LINE)s; vertical-align:middle; }
tr:last-child td { border-bottom:none; }
.tag { background:rgba(255,0,38,.14); color:%(RED)s; font-size:10.5px;
  padding:1px 6px; border-radius:4px; margin-left:6px; white-space:nowrap; }
.zbar { display:flex; height:9px; width:110px; border-radius:5px; overflow:hidden;
  background:%(LINE)s; }
.zbar i { height:100%%; display:inline-block; }
.corrs { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
.corr .sub { margin-top:4px; }
footer { color:%(DIM)s; font-size:12px; margin-top:36px; padding-top:16px;
  border-top:1px solid %(LINE)s; line-height:1.8; }

/* --- 标签页：隐藏 radio 控制面板显隐，零 JS --- */
input[name="tab"] { position:absolute; opacity:0; pointer-events:none; }
/* 底栏固定在视口底部，拇指够得着；仍是 radio + label，没有一行 JS */
.tabs { position:fixed; left:0; right:0; bottom:0; z-index:30; margin:0;
  display:grid; grid-template-columns:repeat(4,1fr);
  background:rgba(12,13,15,.93); -webkit-backdrop-filter:blur(16px);
  backdrop-filter:blur(16px); border-top:1px solid %(LINE)s;
  padding-bottom:env(safe-area-inset-bottom); }
.tabl { display:flex; flex-direction:column; align-items:center; gap:3px;
  padding:9px 4px 8px; font-size:11px; color:%(DIM)s; cursor:pointer;
  user-select:none; -webkit-tap-highlight-color:transparent;
  transition:color .15s; }
.tabl svg { width:23px; height:23px; fill:none; stroke:currentColor;
  stroke-width:1.7; stroke-linecap:round; stroke-linejoin:round; }
.panel { display:none; }
#tab-home:checked ~ .panel.p-home,
#tab-sleep:checked ~ .panel.p-sleep,
#tab-body:checked ~ .panel.p-body,
#tab-trend:checked ~ .panel.p-trend { display:block; }
#tab-home:checked ~ .tabs .tabl[for="tab-home"],
#tab-sleep:checked ~ .tabs .tabl[for="tab-sleep"],
#tab-body:checked ~ .tabs .tabl[for="tab-body"],
#tab-trend:checked ~ .tabs .tabl[for="tab-trend"] { color:%(FG)s; }
.panel > h2:first-child { margin-top:6px; }
.tips { display:flex; flex-direction:column; gap:9px; margin-top:4px; }
.tip { display:flex; align-items:flex-start; gap:9px; font-size:13px; line-height:1.65; }
.tip i { width:8px; height:8px; border-radius:50%%; margin-top:7px; flex:0 0 8px; }
.tip b { flex:0 0 auto; font-weight:600; }
.tip span { color:%(DIM)s; }
.hbar.tall { height:26px; border-radius:6px; }

@media (max-width: 780px) {
  .hcard { padding:12px 6px; }
  .ring { width:76px; height:76px; }
  .corrs { grid-template-columns:1fr; }
  .wrap { padding:20px 12px 48px; }
  h1 { font-size:22px; }
  .big { font-size:27px; }
  .st { flex:1 1 44%%; }
  /* 图比屏宽，提示可以横向滑动 */
  .chartbox { position:relative; }
  .chartbox::after { content:"左右滑动查看完整趋势"; display:block;
    color:%(DIM)s; font-size:11px; text-align:right; padding:2px 2px 0; }
  table { min-width:560px; }
  .tabl { font-size:10.5px; }
}
@media (min-width: 781px) { .chartbox::after { display:none; } }
.chartbox.fits::after { display:none; }

/* 图比屏幕宽时，默认停在最右边 —— 打开第一眼该看到「今天」，
   而不是三周前。rtl 让滚动容器的初始位置落在右端，内部再翻回 ltr，
   纯 CSS 就够，不需要脚本去设 scrollLeft。 */
.chartbox { direction: rtl; }
.chartbox > svg, .chartbox > table { direction: ltr; }
.chartbox::after { text-align: left; }
/* 放得下的图本来就不用滚，rtl 只会把它推到右边去，改回正常方向居中 */
.chartbox.fits { direction: ltr; }
.chartbox.fits::after { text-align: right; }

/* ══════════ 视觉层 ══════════
   参照 WHOOP App 的观感：深黑底、大数字、宽字距的小标题、当日高亮。
   全部用 CSS 实现，页面仍然一行 JavaScript 都没有。 */

:root {
  /* 数字与标题专用栈。中西混排下最协调的选择就是系统字体：
     PingFang SC 的西文伴侣本来就是 SF Pro，字面大小、基线、字重
     由苹果配套对齐过，换任何第三方拉丁字体都会露出接缝。 */
  --num: -apple-system, "SF Pro Display", "Helvetica Neue", "PingFang SC", sans-serif;
}

/* 背景不是一块死黑：顶部压一层极淡的冷光，往下沉进近黑，页面才有纵深 */
body {
  background:
    radial-gradient(132%% 56%% at 50%% -8%%, rgba(0,147,231,.14), transparent 62%%),
    radial-gradient(88%% 38%% at 92%% 3%%, rgba(22,236,6,.045), transparent 58%%),
    #06070A;
  background-attachment: fixed;
}
/* 一层几乎看不见的颗粒，消掉大面积纯色的塑料感 */
body::after {
  content: ""; position: fixed; inset: 0; z-index: 50; pointer-events: none; opacity: .03;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/></filter><rect width='100%%' height='100%%' filter='url(%%23n)'/></svg>");
}
.wrap { position: relative; z-index: 2; }

/* 数字是这一页的主角：等宽字形保证跳动时不串位，字距收紧才有力量 */
.big, .ringnum, .st b, .vlab, .ax, .gtxt b, .debt b, .hval, .hlab, h1, h2, h3, h4, .tabl {
  font-family: var(--num);
}
.big, .ringnum, .gtxt b, .st b, .vlab { font-variant-numeric: tabular-nums; }
.ringnum, .big, .gtxt b { letter-spacing: -.025em; font-weight: 700; }
h1 { font-weight: 700; letter-spacing: -.01em; }
h2 { letter-spacing: .07em; font-weight: 600; }
h3 { letter-spacing: .1em; font-size: 12.5px; font-weight: 600; text-transform: uppercase; }
.hlab { letter-spacing: .1em; text-transform: uppercase; font-size: 11.5px; }
.st span, .sub, .meta, .cover { letter-spacing: .01em; }

/* 卡片：顶沿一道高光 + 自上而下微微变暗，才像一块实体而不是一个色块 */
.card {
  background: linear-gradient(180deg, #191C21 0%%, #131519 100%%);
  border: 1px solid rgba(255,255,255,.055);
  border-radius: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.05), 0 2px 12px rgba(0,0,0,.34);
  transition: border-color .25s;
}

/* 首页三个环平时裸露在背景上（和 App 一样），只有展开解读时才收拢成一张卡 */
.hcard { background: none; border: none; box-shadow: none; padding: 4px 2px 8px; }
.hcard:has(.exp[open]) {
  background: linear-gradient(180deg, #191C21 0%%, #131519 100%%);
  border: 1px solid rgba(255,255,255,.055);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.05);
  padding: 14px 14px 12px;
}

/* 入场：一次编排好的依次浮起，比满屏零散的微交互更有质感 */
@keyframes rise { from { opacity: 0; transform: translateY(12px); } }
.hero .hcard, .panel > * { animation: rise .46s cubic-bezier(.22,1,.36,1) backwards; }
.hero .hcard:nth-child(1) { animation-delay: .05s; }
.hero .hcard:nth-child(2) { animation-delay: .11s; }
.hero .hcard:nth-child(3) { animation-delay: .17s; }
.p-home > *:nth-child(1) { animation-delay: .22s; }
.p-home > *:nth-child(2) { animation-delay: .28s; }
.p-home > *:nth-child(3) { animation-delay: .34s; }
.p-home > *:nth-child(n+4) { animation-delay: .40s; }

/* 圆环顺时针画出来——这是整页唯一一处「表演」，放在最该看的地方 */
@keyframes draw { from { stroke-dashoffset: var(--circ); } }
.ring .arc { animation: draw 1.15s cubic-bezier(.32,1,.38,1) backwards; animation-delay: .3s; }

/* 展开解读、切换面板都用淡入收尾，不硬切 */
@keyframes fade { from { opacity: 0; transform: translateY(-5px); } }
.exp[open] .expc { animation: fade .26s ease; }
.panel { animation: fade .28s ease; }

/* 底栏：选中项顶上亮一小段，替代传统下划线 */
.tabl::before {
  content: ""; position: absolute; top: 0; left: 50%%; transform: translateX(-50%%);
  width: 0; height: 2px; border-radius: 0 0 3px 3px; background: %(BLUE)s;
  transition: width .24s cubic-bezier(.22,1,.36,1);
}
#tab-home:checked ~ .tabs .tabl[for="tab-home"]::before,
#tab-sleep:checked ~ .tabs .tabl[for="tab-sleep"]::before,
#tab-body:checked ~ .tabs .tabl[for="tab-body"]::before,
#tab-trend:checked ~ .tabs .tabl[for="tab-trend"]::before { width: 26px; }
.tabl:active { transform: scale(.93); }
.exp summary:active { opacity: .55; }
.rfs { transition: background .2s, border-color .2s; }

/* 尊重系统的减少动效设置 */
@media (prefers-reduced-motion: reduce), print {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
""" % {"BG": BG, "FG": FG, "CARD": CARD, "LINE": LINE, "DIM": DIM,
       "BLUE": BLUE, "YELLOW": YELLOW, "RED": RED, "GREEN": GREEN, "ORANGE": POOR}


TAB_ICONS = {
    "home": '<path d="M4 10.6 12 4l8 6.6V20h-5.5v-5.5h-5V20H4z"/>',
    "sleep": '<path d="M20.5 13.4A8.5 8.5 0 1 1 10.6 3.5a6.7 6.7 0 0 0 9.9 9.9z"/>',
    "body": '<path d="M3 12h3.4l2.1-5.8 3.5 11.6 2.4-7 1.6 3.2H21"/>',
    "trend": '<path d="M3 17.5 9 11l4 4 8-8.6"/><path d="M16.6 6.4H21v4.4"/>',
}


def render(days, raw, refresh=False):
    # 纯 CSS 标签页：隐藏的 radio + label，不用一行 JavaScript，
    # 离线打开、手机上都能正常切换。
    tabs = [("home", "今日"), ("sleep", "睡眠"), ("body", "身体"), ("trend", "趋势")]
    radios = "".join(
        f'<input type="radio" name="tab" id="tab-{k}"{" checked" if i == 0 else ""}>'
        for i, (k, _) in enumerate(tabs))
    nav = "".join(
        f'<label for="tab-{k}" class="tabl">'
        f'<svg viewBox="0 0 24 24">{TAB_ICONS[k]}</svg>{esc(n)}</label>' for k, n in tabs)

    panel_home = sec_home(days, raw)
    panel_sleep = sec_sleep(days)          # 时长分期 / 质量评分 / 睡眠债 / 作息规律
    panel_body = (sec_recovery(days) + sec_strain(days)
                  + sec_workouts(days) + sec_correlation(days))
    panel_trend = ('<h2>周统计</h2>' + sec_week(days)
                   + '<h2>月统计</h2>' + sec_month(days))

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0A0A0A">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="健康工作台">
<title>WHOOP 健康工作台</title>
<style>{CSS}</style></head>
<body>
<svg width="0" height="0" aria-hidden="true" style="position:absolute">
  <defs><linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#fff" stop-opacity=".20"/>
    <stop offset=".5" stop-color="#fff" stop-opacity=".05"/>
    <stop offset="1" stop-color="#fff" stop-opacity="0"/>
  </linearGradient></defs>
</svg>
<div class="wrap">
{radios}
{sec_header(days, raw, refresh)}
<div class="panel p-home">{panel_home}</div>
<div class="panel p-sleep">{panel_sleep}</div>
<div class="panel p-body">{panel_body}</div>
<div class="panel p-trend">{panel_trend}</div>
<footer>
开发联系：<a href="mailto:bleetchenxuanling@gmail.com">bleetchenxuanling@gmail.com</a><br>
数据来源：WHOOP API v2 · 共 {len(raw["cycles"])} 条生理周期 / {len(raw["sleep"])} 条睡眠 /
{len(raw["workouts"])} 条运动 / {len(raw["recovery"])} 条恢复记录<br>
WHOOP 开放接口不提供逐分钟心率原始序列，本页已用尽可获取的全部字段。<br>
本页由 report.py 自动生成，每次同步后覆盖。<br>
各项解读用于帮助读懂这些数字，不构成医疗建议；身体有不适请以医生判断为准。
</footer>
<nav class="tabs">{nav}</nav>
</div></body></html>"""


def write_atomic(path, text):
    """先写 tmp 再 rename，避免读到写了一半的文件。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + f".tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="生成 WHOOP 健康工作台")
    ap.add_argument("--data", default=DEFAULT_DATA, help="数据目录")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出的 HTML 路径")
    ap.add_argument("--refresh", action="store_true",
                    help="页头加查看最新页面链接（不会触发API同步）")
    a = ap.parse_args()

    raw = load(a.data)
    days = build_days(raw)
    write_atomic(a.out, render(days, raw, a.refresh))
    print(f"工作台已生成：{a.out}（{len(days)} 天）")


if __name__ == "__main__":
    main()
