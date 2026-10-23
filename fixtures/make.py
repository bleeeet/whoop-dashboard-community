#!/usr/bin/env python3
"""生成渲染验证用的构造数据：empty / multi 两套；不读取任何真实数据。"""

import json
import math
import os
import random
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DAYS = 21
USER = 10000001
TZ = "+08:00"

random.seed(20260831)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write(dirname, name, obj):
    d = os.path.join(BASE, dirname)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".json"), "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


PROFILE = {"user_id": USER, "email": "demo@example.com",
           "first_name": "Demo", "last_name": "User"}
BODY = {"height_meter": 1.75, "weight_kilogram": 65.0, "max_heart_rate": 185}


def make_multi():
    """21 天。含 1 天缺睡眠、1 天 PENDING_SCORE、1 次小睡、2 次运动。"""
    cycles, recoveries, sleeps, workouts = [], [], [], []
    # 基准日：cycle 从当地 00:00 开始，UTC 记为前一天 16:00
    end_day = datetime(2026, 8, 31)

    for i in range(DAYS):
        day = end_day - timedelta(days=DAYS - 1 - i)
        cid = 1759000000 + i
        start_utc = datetime(day.year, day.month, day.day) - timedelta(hours=8)

        # 睡眠质量走正弦波 + 噪声，让关联分析能验出非零 r
        wave = math.sin(i / 3.2)
        sleep_h = 6.6 + wave * 1.05 + random.uniform(-0.25, 0.25)
        total_ms = int(sleep_h * 3600_000)
        deep = int(total_ms * (0.21 + wave * 0.02))
        rem = int(total_ms * (0.26 + wave * 0.015))
        light = total_ms - deep - rem
        awake = int(random.uniform(18, 46) * 60_000)

        pending = (i == DAYS - 4)   # 这天分数还没算出来
        no_sleep = (i == DAYS - 9)  # 这天没戴手环睡觉

        # --- cycle ---
        strain = 9.5 + wave * 3.2 + random.uniform(-1.2, 1.2)
        cycles.append({
            "id": cid, "user_id": USER,
            "created_at": iso(start_utc + timedelta(hours=22)),
            "updated_at": iso(start_utc + timedelta(hours=22)),
            "start": iso(start_utc),
            "end": None if i == DAYS - 1 else iso(start_utc + timedelta(hours=24)),
            "timezone_offset": TZ,
            "score_state": "PENDING_SCORE" if pending else "SCORED",
            "score": None if pending else {
                "strain": round(strain, 4),
                "kilojoule": round(8200 + strain * 320, 4),
                "average_heart_rate": int(68 + wave * 5),
                "max_heart_rate": int(138 + wave * 22),
            },
        })

        # --- recovery：睡得好 → 次日恢复高（滞后一天，构造相关性）---
        if not pending and not no_sleep:
            prev_wave = math.sin((i - 1) / 3.2)
            rec = 58 + prev_wave * 22 + random.uniform(-6, 6)
            recoveries.append({
                "cycle_id": cid, "sleep_id": f"sleep-{cid}", "user_id": USER,
                "created_at": iso(start_utc + timedelta(hours=1)),
                "updated_at": iso(start_utc + timedelta(hours=1)),
                "score_state": "SCORED",
                "score": {
                    "user_calibrating": i < 4,
                    "recovery_score": round(max(5, min(99, rec))),
                    "resting_heart_rate": round(52 - prev_wave * 3 + random.uniform(-1.5, 1.5)),
                    "hrv_rmssd_milli": round(58 + prev_wave * 12 + random.uniform(-5, 5), 3),
                    "spo2_percentage": round(96.4 + random.uniform(0, 1.6), 3),
                    "skin_temp_celsius": round(33.2 + random.uniform(-0.4, 0.5), 2),
                },
            })

        # --- sleep：上床时间在 22:20~00:10 之间漂移 ---
        if not no_sleep:
            bed_min = 22 * 60 + 20 + int(wave * 42) + random.randint(-18, 18)
            bed_local = datetime(day.year, day.month, day.day) - timedelta(days=1) \
                + timedelta(minutes=bed_min)
            bed_utc = bed_local - timedelta(hours=8)
            in_bed = total_ms + awake
            sleeps.append({
                "id": f"sleep-{cid}", "cycle_id": cid, "user_id": USER,
                "created_at": iso(bed_utc + timedelta(hours=9)),
                "updated_at": iso(bed_utc + timedelta(hours=9)),
                "start": iso(bed_utc),
                "end": iso(bed_utc + timedelta(milliseconds=in_bed)),
                "timezone_offset": TZ, "nap": False,
                "score_state": "PENDING_SCORE" if pending else "SCORED",
                "score": None if pending else {
                    "stage_summary": {
                        "total_in_bed_time_milli": in_bed,
                        "total_awake_time_milli": awake,
                        "total_no_data_time_milli": 0,
                        "total_light_sleep_time_milli": light,
                        "total_slow_wave_sleep_time_milli": deep,
                        "total_rem_sleep_time_milli": rem,
                        "sleep_cycle_count": max(2, round(sleep_h / 1.5)),
                        "disturbance_count": random.randint(4, 17),
                    },
                    "sleep_needed": {
                        "baseline_milli": 28_080_000,
                        "need_from_sleep_debt_milli": int(max(0, (7.6 - sleep_h)) * 1800_000),
                        "need_from_recent_strain_milli": int(strain * 78_000),
                        "need_from_recent_nap_milli": 0,
                    },
                    "respiratory_rate": round(14.4 + random.uniform(-0.7, 0.7), 4),
                    "sleep_performance_percentage": round(min(100, sleep_h / 7.8 * 100)),
                    "sleep_consistency_percentage": round(64 + wave * 14 + random.uniform(-6, 6)),
                    "sleep_efficiency_percentage": round(total_ms / in_bed * 100, 4),
                },
            })

    # --- 一次小睡 ---
    nap_cid = cycles[-5]["id"]
    nap_utc = datetime(2026, 8, 27, 5, 40)  # 当地 13:40
    sleeps.append({
        "id": f"nap-{nap_cid}", "cycle_id": nap_cid, "user_id": USER,
        "created_at": iso(nap_utc + timedelta(hours=2)), "updated_at": iso(nap_utc + timedelta(hours=2)),
        "start": iso(nap_utc), "end": iso(nap_utc + timedelta(minutes=38)),
        "timezone_offset": TZ, "nap": True, "score_state": "SCORED",
        "score": {
            "stage_summary": {
                "total_in_bed_time_milli": 2_280_000, "total_awake_time_milli": 240_000,
                "total_no_data_time_milli": 0, "total_light_sleep_time_milli": 1_500_000,
                "total_slow_wave_sleep_time_milli": 540_000, "total_rem_sleep_time_milli": 0,
                "sleep_cycle_count": 1, "disturbance_count": 1,
            },
            "sleep_needed": {"baseline_milli": 28_080_000, "need_from_sleep_debt_milli": 0,
                             "need_from_recent_strain_milli": 0, "need_from_recent_nap_milli": -1_800_000},
            "respiratory_rate": 14.1, "sleep_performance_percentage": 8,
            "sleep_consistency_percentage": 0, "sleep_efficiency_percentage": 89.4,
        },
    })

    # --- 两次运动，其中一次记录不完整 ---
    for n, (dt_local, sport, sid, mins, st, hr, dist, pct) in enumerate([
        (datetime(2026, 8, 26, 18, 30), "Running", 0, 42, 9.84, 148, 7420.0, 98.6),
        (datetime(2026, 8, 29, 20, 5), "Weightlifting", 45, 55, 7.12, 121, None, 72.3),
    ]):
        w_utc = dt_local - timedelta(hours=8)
        workouts.append({
            "id": f"workout-{n}", "v1_id": 100 + n, "user_id": USER,
            "created_at": iso(w_utc + timedelta(hours=1)), "updated_at": iso(w_utc + timedelta(hours=1)),
            "start": iso(w_utc), "end": iso(w_utc + timedelta(minutes=mins)),
            "timezone_offset": TZ, "sport_name": sport, "sport_id": sid,
            "score_state": "SCORED",
            "score": {
                "strain": st, "average_heart_rate": hr, "max_heart_rate": hr + 32,
                "kilojoule": round(mins * 27.4, 2), "percent_recorded": pct,
                "distance_meter": dist, "altitude_gain_meter": 46.2 if dist else None,
                "altitude_change_meter": 3.1 if dist else None,
                "zone_durations": {
                    "zone_zero_milli": int(mins * 60_000 * 0.05),
                    "zone_one_milli": int(mins * 60_000 * 0.12),
                    "zone_two_milli": int(mins * 60_000 * 0.28),
                    "zone_three_milli": int(mins * 60_000 * 0.35),
                    "zone_four_milli": int(mins * 60_000 * 0.17),
                    "zone_five_milli": int(mins * 60_000 * 0.03),
                },
            },
        })

    return cycles, recoveries, sleeps, workouts


def main():
    for name, obj in (("cycles", []), ("recovery", []), ("sleep", []), ("workouts", []),
                      ("profile", {}), ("body", {})):
        write("empty", name, obj)

    c, r, s, w = make_multi()
    print(f"empty  6 个空文件")
    for name, obj in zip(("cycles", "recovery", "sleep", "workouts", "profile", "body"), (c, r, s, w, PROFILE, BODY)):
        write("multi", name, obj)
    print(f"multi  cycles {len(c)} · recovery {len(r)} · sleep {len(s)} · workouts {len(w)}")


if __name__ == "__main__":
    main()
