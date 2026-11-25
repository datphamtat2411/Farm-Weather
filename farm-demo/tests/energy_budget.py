"""Chapter 3 design budget, not measured current or battery runtime."""
import json
def budget(period_s=300):
    if period_s not in (300,900):
        raise ValueError("unsupported period")
    # Acquisition/wake budget scales with time; one RF handoff per report.
    states = [
        {"state":"active","current_ma":12,"seconds":12*period_s/300},
        {"state":"tx","current_ma":140,"seconds":2},
        {"state":"rx","current_ma":18,"seconds":3},
        {"state":"stop2","current_ma":0.30,"seconds":period_s-12*period_s/300-5},
    ]
    for state in states:
        state["ma_s"]=state["current_ma"]*state["seconds"]
    avg=sum(s["ma_s"] for s in states)/period_s
    measurement=3.3*avg/1000*24
    alarm=3.3*(0.030+0.005)*0.25
    battery=(measurement+alarm)/0.85+3.2*0.001*24
    capacity=battery*3/(3.2*0.8*0.8)
    return {"period_s":period_s,"states":states,"average_ma":avg,
            "measurement_wh_day":measurement,"alarm_wh_day":alarm,
            "battery_wh_day":battery,"minimum_ah":capacity,"design_ah":capacity*1.5,
            "selected_ah":2,"solar_wh_day":5*2*0.5}
def report():
    normal,low=budget(300),budget(900)
    return {"kind":"engineering_budget_not_measurement","rounding_note":"Report rounds daily battery energy to 0.286 Wh before deriving 0.419 Ah; unrounded calculation retained here.","normal":normal,"low_power":low,
            "measurement_savings_percent":100*(1-low["measurement_wh_day"]/normal["measurement_wh_day"]),
            "gateway_minimum_ah":192/(12.8*0.9*0.8),"gateway_selected_ah":25}
if __name__=="__main__":
    print(json.dumps(report(),indent=2,allow_nan=False))
