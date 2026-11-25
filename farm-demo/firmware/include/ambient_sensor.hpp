#pragma once
#include <Arduino.h>
#include <math.h>
#include "acquisition.hpp"

// Bounded open-drain software I2C adapter for the Wokwi F103 peripheral model.
// Register transactions are real SCL/SDA transitions, never direct value injection.
// Explicit input-pullup/output-low modes are required by the simulated F103 GPIO.
// Direct BSRR open-drain writes failed real Wokwi FLASH_RECOVERY acceptance.
// The production STM32L476 uses its hardware I2C peripheral (Task 03).
namespace farm {
class SimI2c {
    uint32_t sda,scl;
    static void release(uint32_t pin){pinMode(pin,INPUT_PULLUP);}
    static void low(uint32_t pin){digitalWrite(pin,LOW);pinMode(pin,OUTPUT);}
    static void half(){delayMicroseconds(5);}
    bool clockHigh(){
        release(scl);
        const uint32_t start=micros();
        while(!digitalRead(scl)){
            if(uint32_t(micros()-start)>=1000)return false;
        }
        half();return true;
    }
    bool start(){
        release(sda);
        if(!clockHigh()||!digitalRead(sda))return false;
        low(sda);half();low(scl);half();return true;
    }
    void stop(){
        low(sda);half();clockHigh();release(sda);half();
    }
    bool put(uint8_t byte){
        for(unsigned i=0;i<8;++i){
            if(byte&0x80)release(sda);else low(sda);
            half();if(!clockHigh())return false;
            low(scl);half();byte<<=1;
        }
        release(sda);half();if(!clockHigh())return false;
        const bool ack=!digitalRead(sda);low(scl);half();return ack;
    }
    bool get(uint8_t& byte,bool ack){
        release(sda);byte=0;
        for(unsigned i=0;i<8;++i){
            half();if(!clockHigh())return false;
            byte=uint8_t((byte<<1)|(digitalRead(sda)?1:0));low(scl);half();
        }
        if(ack)low(sda);else release(sda);
        half();if(!clockHigh())return false;
        low(scl);half();release(sda);return true;
    }
public:
    SimI2c(uint32_t data,uint32_t clock):sda(data),scl(clock){}
    void begin(){release(sda);release(scl);}
    bool memoryCommand(uint8_t command,unsigned address,const uint8_t* data,unsigned n){
        bool ok=start()&&put(0x50<<1)&&put(command)&&put(uint8_t(address>>8))&&put(uint8_t(address));
        for(unsigned i=0;ok&&i<n;++i)ok=put(data[i]);
        stop();return ok;
    }
    bool memoryRead(unsigned address,uint8_t* data,unsigned n,uint8_t command=0){
        if(!memoryCommand(command,address,nullptr,0))return false;
        bool ok=start()&&put((0x50<<1)|1);
        for(unsigned i=0;ok&&i<n;++i)ok=get(data[i],i+1<n);
        stop();return ok;
    }
    bool write(uint8_t reg,uint8_t value){
        bool ok=start()&&put(0x76<<1)&&put(reg)&&put(value);stop();return ok;
    }
    bool read(uint8_t reg,uint8_t* buf,unsigned n){
        bool ok=start()&&put(0x76<<1)&&put(reg);stop();
        if(!ok)return false;
        ok=start()&&put((0x76<<1)|1);
        for(unsigned i=0;ok&&i<n;++i)ok=get(buf[i],i+1<n);
        stop();return ok;
    }
};

class AmbientSensor {
    SimI2c& bus;
    uint16_t t1=0,p1=0;
    int16_t t2=0,t3=0,p2=0,p3=0,p4=0,p5=0,p6=0,p7=0,p8=0,p9=0,h2=0,h4=0,h5=0;
    uint8_t h1=0,h3=0;
    int8_t h6=0;
    static uint16_t u16(const uint8_t* p){return uint16_t(p[0])|(uint16_t(p[1])<<8);}
    static int16_t s12(uint16_t x){return x&0x800?int16_t(x)-4096:int16_t(x);}
    static uint32_t u20(const uint8_t* p){return (uint32_t(p[0])<<12)|(uint32_t(p[1])<<4)|(p[2]>>4);}
    bool waitIdle(uint8_t mask){
        for(unsigned i=0;i<50;++i){
            uint8_t status=0;
            if(!bus.read(0xf3,&status,1))return false;
            if(!(status&mask))return true;
            delay(1);
        }
        return false;
    }
public:
    explicit AmbientSensor(SimI2c& wire):bus(wire){}
    bool begin(){
        bus.begin();
        uint8_t id=0,c[24],h[7];
        if(!bus.read(0xd0,&id,1)||id!=0x60||!bus.write(0xe0,0xb6))return false;
        delay(3);
        if(!waitIdle(1)||!bus.read(0x88,c,sizeof(c))||!bus.read(0xa1,&h1,1)||!bus.read(0xe1,h,sizeof(h)))return false;
        t1=u16(c);t2=int16_t(u16(c+2));t3=int16_t(u16(c+4));p1=u16(c+6);
        p2=int16_t(u16(c+8));p3=int16_t(u16(c+10));p4=int16_t(u16(c+12));
        p5=int16_t(u16(c+14));p6=int16_t(u16(c+16));p7=int16_t(u16(c+18));
        p8=int16_t(u16(c+20));p9=int16_t(u16(c+22));
        h2=int16_t(u16(h));h3=h[2];h4=s12((uint16_t(h[3])<<4)|(h[4]&15));
        h5=s12((uint16_t(h[5])<<4)|(h[4]>>4));h6=int8_t(h[6]);
        return p1!=0&&bus.write(0xf2,1)&&bus.write(0xf5,0);
    }
    bool read(AmbientSample& sample){
        uint8_t raw[8];
        if(!bus.write(0xf4,0x25)||!waitIdle(8)||!bus.read(0xf7,raw,8))return false;
        const double adcT=u20(raw+3),adcP=u20(raw),adcH=(uint16_t(raw[6])<<8)|raw[7];
        if(adcT==0x80000||adcP==0x80000||adcH==0x8000)return false;
        const double dt=adcT/16384.0-t1/1024.0;
        const double fine=dt*t2+(adcT/131072.0-t1/8192.0)*(adcT/131072.0-t1/8192.0)*t3;
        double v1=fine/2-64000,v2=v1*v1*p6/32768.0+v1*p5*2;
        v2=v2/4+p4*65536.0;
        v1=(p3*v1*v1/524288.0+p2*v1)/524288.0;
        v1=(1+v1/32768.0)*p1;
        if(v1==0)return false;
        double p=(1048576-adcP-v2/4096)*6250.0/v1;
        p+=(p9*p*p/2147483648.0+p8*p/32768.0+p7)/16.0;
        double rh=fine-76800.0;
        rh=(adcH-(h4*64.0+h5/16384.0*rh))*(h2/65536.0*(1+h6/67108864.0*rh*(1+h3/67108864.0*rh)));
        rh*=1-h1*rh/524288.0;
        if(rh<0)rh=0;if(rh>100)rh=100;
        sample={float(fine/5120.0),float(rh),float(p/100)};
        return isfinite(sample.temperature)&&isfinite(sample.humidity)&&isfinite(sample.pressure);
    }
};
}
