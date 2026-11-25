#pragma once
#include <Adafruit_BME280.h>
#include <Wire.h>
namespace farm {
struct AmbientSample {float temperature,humidity,pressure;};
class AmbientSensor {
    Adafruit_BME280 sensor;
public:
    bool begin(TwoWire& wire) {
        if(!sensor.begin(0x76,&wire))return false;
        sensor.setSampling(Adafruit_BME280::MODE_FORCED,
            Adafruit_BME280::SAMPLING_X1,Adafruit_BME280::SAMPLING_X1,
            Adafruit_BME280::SAMPLING_X1,Adafruit_BME280::FILTER_OFF);
        return true;
    }
    bool read(AmbientSample& sample) {
        if(!sensor.takeForcedMeasurement())return false;
        sample={sensor.readTemperature(),sensor.readHumidity(),sensor.readPressure()/100.0f};
        return isfinite(sample.temperature)&&isfinite(sample.humidity)&&isfinite(sample.pressure);
    }
};
}
