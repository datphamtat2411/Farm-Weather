#include <Arduino.h>
#include "physical_pins.hpp"
#include "weather.hpp"
#include "power.hpp"
#include "wire.hpp"

#if !defined(FARM_PHYSICAL_COMPILE_PROBE) || !defined(STM32L476xx)
#error This non-deployable compile probe must target STM32L476RG.
#endif
// Compile/link only. No synthetic sensor values are presented as physical data.
// No GPIO, actuator, serial, RF or flash access. Real drivers belong to Tasks 02+.
farm::Station probe;
farm::LocalActuation localAlarmProbe;
farm::AmbientProcessor pipeline;
farm::DirectionAccumulator direction;
volatile unsigned probe_direction;
volatile unsigned compile_probe_status;
volatile unsigned compile_probe_wire_size;
void setup() {
    farm::PowerMonitor powerProbe;
    powerProbe.begin(0);powerProbe.sleep(1);powerProbe.wakePulse(2,true);powerProbe.wakeRtc(3);
    probe.tick(0,0);
    pipeline.ingest({25.0f,60.0f,1000.0f},true);
    direction.add(0);direction.add(45);
    unsigned angle=0;
    if(direction.mean(angle))probe_direction=angle;
    localAlarmProbe.update(probe.seconds,probe.status,probe.windReady);
    // Link the same bounded codec on the report's physical target.
    farm::wire::Record record{};
    record.sequence=probe.seconds;record.ts=1795453200;record.period_s=probe.activePeriod;
    record.valid=pipeline.mask();record.temperature=farm::wire::x10(pipeline.temperature.filtered);
    record.humidity=unsigned(farm::wire::x10(pipeline.humidity.filtered));
    record.pressure=unsigned(farm::wire::x10(pipeline.pressure.filtered));
    const auto payload=farm::wire::encode(record);
    compile_probe_wire_size=payload.ok?payload.size:0;
    compile_probe_status=probe.status | unsigned(localAlarmProbe.stopRequest) | powerProbe.pulse_wakes;
}
void loop() {}
