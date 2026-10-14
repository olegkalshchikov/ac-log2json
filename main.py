import re
import json
import os

def time_to_ms(time_str):
    try:
        time_str = time_str.strip()
        parts = list(map(int, time_str.split(':')))
        if len(parts) == 3: return parts[0] * 60000 + parts[1] * 1000 + parts[2]
        elif len(parts) == 4: return parts[0] * 3600000 + parts[1] * 60000 + parts[2] * 1000 + parts[3]
    except: return 0
    return 0

def parse_ac_log(file_path):
    if not os.path.exists(file_path):
        print(f"Ошибка: Файл {file_path} не найден.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    global_driver_registry = {}  # name -> details (обновляется каскадно)
    car_id_to_name = {}          # car_id -> name
    
    collecting_pilots = False
    pending_connection = None
    race_count = 0
    current_race = None 

    for i, line in enumerate(lines):
        line = line.strip()

        # --- ШАГ 1: СБОР И ОБНОВЛЕНИЕ МЕТАДАННЫХ ПИЛОТОВ ---
        if "Opening entry list:" in line:
            collecting_pilots = True
            continue
        if "NextSession" in line:
            collecting_pilots = False

        if collecting_pilots and line.startswith("CAR:"):
            main_match = re.search(r"CAR:\s+\d+\s+(\S+)\s+\((\d+)\)\s+\[.*?\]\s+(.*?)\s+\[\]\s+(\d+)", line)
            if main_match:
                model, car_id, name, steam_id = main_match.groups()
                name, car_id = name.strip(), int(car_id)
                global_driver_registry[name] = {
                    "DriverName": name, "DriverGuid": steam_id, "CarId": car_id,
                    "CarModel": model, "BallastKG": 0, "Restrictor": 0
                }
                car_id_to_name[car_id] = name
            
            look_ahead = i + 1
            while look_ahead < len(lines) and look_ahead < i + 20:
                next_line = lines[look_ahead].strip()
                if "Ballast:" in next_line:
                    b_match = re.search(r"Ballast:\s+(\d+)\s+kg", next_line)
                    n_match = re.search(r"\]\s+(.*?)\s+\[\]", next_line)
                    if b_match and n_match:
                        n = n_match.group(1).strip()
                        if n in global_driver_registry: global_driver_registry[n]["BallastKG"] = int(b_match.group(1))
                if "Restrictor:" in next_line:
                    r_match = re.search(r"Restrictor:\s+(\d+)", next_line)
                    n_match = re.search(r"\]\s+(.*?)\s+\[\]", next_line)
                    if r_match and n_match:
                        n = n_match.group(1).strip()
                        if n in global_driver_registry: global_driver_registry[n]["Restrictor"] = int(r_match.group(1))
                look_ahead += 1

        if "NEW PICKUP CONNECTION" in line:
            pending_connection = {"name": None, "car": None, "guid": None, "car_id": None}
        elif pending_connection:
            if "REQUESTED CAR:" in line:
                pending_connection["car"] = line.split(":")[1][:-1].strip()
            elif "GUID" in line:
                guid_match = re.search(r"GUID\s+(\d+)", line)
                if guid_match: pending_connection["guid"] = guid_match.group(1)
            elif "DRIVER ACCEPTED FOR CAR" in line:
                idata = line.split(" ", 4)
                if idata[-1].isdigit():
                    pending_connection["car_id"] = int(idata[-1])
                else:
                    pending_connection["name"] = idata[-1]
                
                if pending_connection.get("name") and pending_connection.get("car") and \
                   pending_connection.get("guid") and pending_connection.get("car_id"):
                    name = pending_connection["name"]
                    global_driver_registry[name] = {
                        "DriverName": name, "DriverGuid": pending_connection["guid"],
                        "CarId": pending_connection["car_id"], "CarModel": pending_connection["car"],
                        "BallastKG": 0, "Restrictor": 0
                    }
                    car_id_to_name[pending_connection["car_id"]] = name
                    pending_connection = None

        # --- ШАГ 2: ОБРАБОТКА ГОНКИ ---

        if "RACE, WaitTime:" in line:
            race_count += 1
            current_race = {
                "results": [], 
                "laps": [], 
                "tyres": {}, 
                "sectors": {}, 
                "driver_last_totals": {}, 
                "events": [],
                "driver_lap_counter": {} # Хранит кол-во ЗАВЕРШЕННЫХ кругов
            }

        if current_race:
            # --- ОБРАБОТКА СТОЛКНОВЕНИЙ (COLLISION) ---
            if "COLLISION BETWEEN:" in line:
                coll_match = re.search(
                    r"COLLISION BETWEEN:\s*(.*?)\s*\[\]\s*AND\s*(.*?)\s*\[\]\s*SPEED:\s*([\d.]+)\s*WORLD POS:\s*\[(.*?),?\s*(.*?),?\s*(.*?)\]\s*REL POS:\s*\[(.*?),?\s*(.*?),?\s*(.*?)\]", 
                    line
                )
                if coll_match:
                    name1, name2, speed_ms, _, _, world, _, _, rel = coll_match.groups()
                    wx, wy, wz = world.split(",")
                    rx, ry, rz = rel.split(",")
                    d1 = global_driver_registry.get(name1.strip(), {})
                    d2 = global_driver_registry.get(name2.strip(), {})

                    # Текущий круг = завершенные круги + 1
                    current_lap_for_p1 = current_race["driver_lap_counter"].get(name1, 0) + 1

                    event = {
                        "Type": "COLLISION_WITH_CAR",
                        "Lap": current_lap_for_p1,
                        "CarId": d1.get("CarId"),
                        "Driver": {
                            "Name": name1,
                            "Guid": d1.get("DriverGuid")
                        },
                        "OtherCarId": d2.get("CarId"),
                        "OtherDriver": {
                            "Name": name2,
                            "Guid": d2.get("DriverGuid")
                        },
                        "ImpactSpeed": round(float(speed_ms) / 3.6, 6),
                        "WorldPosition": {
                            "X": float(wx), "Y": float(wy), "Z": float(wz)
                        },
                        "RelPosition": {
                            "X": float(rx), "Y": float(ry), "Z": float(rz)
                        }
                    }
                    current_race["events"].append(event)

            # Сектора
            split_match = re.search(r"Car\.onSplitCompleted\s+(\d+)\s+(\d+)\s+(\d+)", line)
            if split_match:
                cid, s_idx, s_ms = map(int, split_match.groups())
                name = car_id_to_name.get(cid)
                if name:
                    if name not in current_race["sectors"]: current_race["sectors"][name] = {0: 0, 1: 0}
                    current_race["sectors"][name][s_idx] = s_ms

            # Шины
            tyre_match = re.search(r"(.*?) \[\] changed tyres to (\w)", line)
            if tyre_match:
                t_name, t_type = tyre_match.groups()
                current_race["tyres"][t_name.strip()] = t_type

            # Завершение круга (LAP)
            lap_header_match = re.search(r"^LAP\s+(.*?)\s+([\d:]+)$", line)
            if lap_header_match:
                l_name, _ = lap_header_match.groups()
                l_name = l_name.strip()
                
                # Увеличиваем счетчик завершенных кругов для этого пилота
                current_race["driver_lap_counter"][l_name] = current_race["driver_lap_counter"].get(l_name, 0) + 1

                total_ms = 0
                look_ahead = i + 1
                while look_ahead < len(lines) and look_ahead < i + 50:
                    if "SendLapCompletedMessage" in lines[look_ahead]:
                        for k in range(look_ahead + 1, look_ahead + 30):
                            if k >= len(lines): break
                            total_match = re.search(rf"^\d+\)\s+{re.escape(l_name)}\s+BEST:.*?TOTAL:\s+([\d:]+)", lines[k])
                            if total_match:
                                total_ms = time_to_ms(total_match.group(1))
                                break
                        break
                    look_ahead += 1
                
                if l_name in global_driver_registry and total_ms > 0:
                    prev_total = current_race["driver_last_totals"].get(l_name, 0)
                    calculated_lap_time = total_ms - prev_total
                    current_race["driver_last_totals"][l_name] = total_ms

                    s0 = current_race["sectors"].get(l_name, {}).get(0, 0)
                    s1 = current_race["sectors"].get(l_name, {}).get(1, 0)
                    s2 = max(0, calculated_lap_time - (s0 + s1))
                    
                    current_race["laps"].append({
                        "DriverName": l_name, "DriverGuid": global_driver_registry[l_name]["DriverGuid"],
                        "CarId": global_driver_registry[l_name]["CarId"], "CarModel": global_driver_registry[l_name]["CarModel"],
                        "Timestamp": total_ms, "LapTime": calculated_lap_time, "Sectors": [s0, s1, s2],
                        "Cuts": 0, "BallastKG": global_driver_registry[l_name]["BallastKG"],
                        "Tyre": current_race["tyres"].get(l_name, "Unknown"), "Restrictor": global_driver_registry[l_name]["Restrictor"]
                    })

            # Финал гонки (RACE OVER)
            if "RACE OVER PACKET" in line:
                finished_names = set()
                res_idx = i + 1
                while res_idx < len(lines):
                    res_line = lines[res_idx].strip()
                    res_match = re.search(r"^\d+\)\s+(.*?)\s+BEST:\s+([\d:]+).*?TOTAL:\s+([\d:]+).*?Laps:\d+.*?SesID:\d+\s+Rank:(\d+)", res_line)
                    if not res_match: break
                    name, best_str, total_str, rank = res_match.groups()
                    name, total_ms = name.strip(), time_to_ms(total_str)
                    
                    if total_ms > 0 and name in global_driver_registry:
                        data = global_driver_registry[name].copy()
                        data["BestLap"] = time_to_ms(best_str)
                        data["TotalTime"] = total_ms
                        current_race["results"].append(data)
                        finished_names.add(name)
                    res_idx += 1
                
                # Контроль вышедших пилотов
                disconnected_stats = {}
                for lap in current_race["laps"]:
                    name = lap["DriverName"]
                    if name not in finished_names:
                        if name not in disconnected_stats:
                            disconnected_stats[name] = {"total": 0, "best": float('inf')}
                        disconnected_stats[name]["total"] += lap["LapTime"]
                        if lap["LapTime"] < disconnected_stats[name]["best"]:
                            disconnected_stats[name]["best"] = lap["LapTime"]

                for name, stats in disconnected_stats.items():
                    if name in global_driver_registry:
                        data = global_driver_registry[name].copy()
                        data["BestLap"] = int(stats["best"]) if stats["best"] != float('inf') else 0
                        data["TotalTime"] = int(stats["total"])
                        current_race["results"].append(data)

                # Сохранение JSON
                if current_race["results"] or current_race["laps"]:
                    current_race["laps"].sort(key=lambda x: x["Timestamp"])
                    output_filename = f"race_{race_count}.json"
                    with open(output_filename, 'w', encoding='utf-8') as jf:
                        json.dump({
                            "Result": current_race["results"], 
                            "Laps": current_race["laps"],
                            "Events": current_race["events"]
                        }, jf, indent=4, ensure_ascii=False)
                    print(f"Гонка {race_count} сохранена в {output_filename}")
                current_race = None

    print("Обработка завершена.")

if __name__ == "__main__":
    parse_ac_log("output20261012_190840.log")
