#pragma once
#include "ambient_sensor.hpp"
#include "flash_ring.hpp"

namespace farm::sim {
// Custom Wokwi NOR model exposed over the existing I2C bus. Not a physical
// EEPROM/flash driver. Address space survives MCU reset within a simulation.
class Flash {
    SimI2c& bus;
public:
    explicit Flash(SimI2c& b):bus(b){}
    bool blank(bool& empty){
        uint8_t result=0xff;
        if(!bus.memoryRead(0,&result,1,6)||result>1)return false;
        empty=result==1;return true;
    }
    bool scenarioCommand(char* out,unsigned n){
        return bus.readMailbox(out,n);
    }
    bool read(unsigned at,uint8_t* data,unsigned n){
        return n&&at<storage::bytes&&n<=storage::bytes-at&&bus.memoryRead(at,data,n);
    }
    bool program(unsigned at,const uint8_t* data,unsigned n){
        return n&&!(at%8)&&!(n%8)&&at<storage::bytes&&n<=storage::bytes-at&&bus.memoryCommand(1,at,data,n);
    }
    bool commandRead(unsigned at,uint8_t* data,unsigned n){return n&&at<4096&&n<=4096-at&&bus.memoryRead(at,data,n,4);}
    bool commandProgram(unsigned at,const uint8_t* data,unsigned n){return n&&!(at%8)&&!(n%8)&&at<4096&&n<=4096-at&&bus.memoryCommand(5,at,data,n);}
    bool readClock(uint32_t& logicalSeconds){
        uint8_t data[4];
        if(!bus.memoryRead(0,data,sizeof(data),3))return false;
        logicalSeconds=storage::load32(data);return true;
    }
    bool erasePage(unsigned at){
        return !(at%storage::page)&&at<storage::bytes&&bus.memoryCommand(2,at,nullptr,0);
    }
};
}
