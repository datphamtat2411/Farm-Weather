#pragma once
#include <Arduino.h>

// Report table 3.3. These are MCU GPIO names, not Arduino header numbers.
namespace farm::physical {
constexpr PinName direction = PA_0, battery = PA_1;
constexpr PinName wind = PB_0, rain = PB_1;
constexpr PinName bme_scl = PB_8, bme_sda = PB_9;
constexpr PinName radio_sck = PA_5, radio_miso = PA_6, radio_mosi = PA_7;
constexpr PinName radio_nss = PB_6, radio_reset = PB_7;
constexpr PinName radio_busy = PC_6, radio_dio1 = PC_7;
constexpr PinName radio_rxen = PC_10, radio_txen = PC_11;
constexpr PinName buzzer = PA_9, relay = PA_8, direction_power = PC_8;
constexpr PinName pins[] = {direction,battery,wind,rain,bme_scl,bme_sda,
    radio_sck,radio_miso,radio_mosi,radio_nss,radio_reset,radio_busy,
    radio_dio1,radio_rxen,radio_txen,buzzer,relay,direction_power};
constexpr bool distinct() {
    for (unsigned i=0; i<sizeof(pins)/sizeof(pins[0]); ++i)
        for (unsigned j=i+1; j<sizeof(pins)/sizeof(pins[0]); ++j)
            if (pins[i] == pins[j]) return false;
    return true;
}
static_assert(distinct(), "Report pin contract contains overlapping GPIO assignments");
}
