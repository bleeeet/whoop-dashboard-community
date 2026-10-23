#!/usr/bin/env python3
"""Generate a private, deterministic weekly digest. No network or model calls."""
import argparse
from datetime import datetime, timedelta, timezone
import math
import report

TZ = timezone(timedelta(hours=8))


def window(now=None):
    current = now or datetime.now(TZ)
    if current.tzinfo is None:
        raise ValueError('now must include timezone')
    monday = current.astimezone(TZ).date()
    monday -= timedelta(days=monday.weekday())
    return monday - timedelta(days=7), monday


def valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def mean(values):
    values = [x for x in values if valid(x)]
    return sum(values) / len(values) if values else None


def summarise(days, raw, start, end):
    selected = [d for d in days if start.isoformat() <= d['date'] < end.isoformat()]
    # At most one daily observation; a repeated API cycle must not inflate coverage.
    selected = list({d['date']: d for d in selected}.values())
    metrics = {
        'recovery': [report.d_rec(d) for d in selected],
        'sleep': [report.d_sleep_total(d) for d in selected],
        'strain': [report.d_strain_done(d) for d in selected],
        'hrv': [(report.d_score(d, 'recovery') or {}).get('hrv_rmssd_milli') for d in selected],
        'rhr': [(report.d_score(d, 'recovery') or {}).get('resting_heart_rate') for d in selected],
    }
    metrics = {k: [x for x in values if valid(x)] for k, values in metrics.items()}
    workouts = 0
    for row in raw.get('workouts', []):
        try:
            when = datetime.fromisoformat(row['start'].replace('Z', '+00:00'))
            if when.tzinfo is None:
                continue
            if start <= when.astimezone(TZ).date() < end and row.get('end'):
                workouts += 1
        except (KeyError, TypeError, ValueError):
            continue
    return {'days': len(selected), 'metrics': metrics, 'workouts': workouts,
            'green': sum(x >= 67 for x in metrics['recovery']),
            'red': sum(x < 34 for x in metrics['recovery'])}


def generate(data_dir, now=None):
    raw = report.load(str(data_dir))
    days = report.build_days(raw)
    start, end = window(now)
    current = summarise(days, raw, start, end)
    previous = summarise(days, raw, start - timedelta(days=7), start)
    rows = []
    for key, title, unit, scale in (
        ('recovery', '平均恢复分', '%', 1), ('sleep', '平均主睡眠', '小时', 3600000),
        ('hrv', '平均 HRV', 'ms', 1), ('rhr', '平均静息心率', 'bpm', 1),
        ('strain', '平均完成日负荷', '', 1)):
        values = current['metrics'][key]
        a, b = mean(values), mean(previous['metrics'][key])
        shown = f'{a / scale:.1f}{unit}' if a is not None else '暂无数据'
        delta = f'{(a-b) / scale:+.1f}{unit}' if a is not None and b is not None else '无可比数据'
        rows.append(f'| {title} | {shown} | {len(values)}/7 | {delta} |')
    result = [f'# WHOOP 每周健康汇总：{start}—{end-timedelta(days=1)}',
              '', '上一完整周（周一至周日）；日历窗口按 UTC+8。', '',
              f"记录覆盖 **{current['days']}/7 天**；有效主睡眠 **{len(current['metrics']['sleep'])}/7 夜**；完成运动 **{current['workouts']} 次**。",
              '', '| 指标 | 本周 | 有效样本 | 与上一完整周比较 |', '| --- | --- | --- | --- |', *rows, '',
              f"恢复绿区 {current['green']} 天，红区 {current['red']} 天。"]
    if current['days'] < 7 or len(current['metrics']['sleep']) < 7:
        result += ['', '本周存在缺失记录，缺失不计为 0；请结合有效样本数阅读均值，不补入周外数据。']
    if current['days'] == 0:
        result += ['', '这一周还没有可用周期记录。请先连接 WHOOP、同步手环，并核对网站最后成功同步时间。']
    result += ['', '负荷均值只包含已结束的周期；主睡眠不含小睡。不同指标变化不自动等于变好或变差。',
               '这是规则统计周报，无需 DeepSeek，不发送原始数据到大模型；不能替代医疗诊断。',
               '', '开发联系：bleetchenxuanling@gmail.com']
    return '\n'.join(result) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--out')
    args = parser.parse_args()
    text = generate(args.data)
    if args.out:
        import whoop
        whoop.atomic_write(args.out, text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
