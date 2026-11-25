#include "commands.hpp"
#include <cassert>
#include <iostream>
using namespace farm;
struct Memory{
    uint8_t data[4096];
    Memory(){memset(data,255,sizeof(data));}
    bool commandRead(unsigned at,uint8_t* b,unsigned n){memcpy(b,data+at,n);return true;}
    bool commandProgram(unsigned at,const uint8_t* b,unsigned n){
        for(unsigned i=0;i<n;++i){if((data[at+i]&b[i])!=b[i])return false;data[at+i]&=b[i];}
        return true;
    }
};
int main(){
    Memory flash;control::Controller<Memory> c(flash);assert(c.begin());
    control::Command cmd;cmd.id[0]=1;cmd.created=100;cmd.expires=200;cmd.method=control::TestBuzzer;
    assert(c.execute(cmd,100,true,true,1).outcome==control::Applied);
    assert(c.testing(3)&&!c.testing(4));
    assert(c.execute(cmd,101,true,true,5).outcome==control::Unknown&&!c.testing(5));
    cmd.id[0]=2;cmd.method=control::SetConfig;cmd.version=2;cmd.on=9;cmd.off=6;cmd.period=900;
    assert(c.execute(cmd,101,true,false,5).reason==control::Unsafe);
    assert(c.execute(cmd,101,true,true,5).outcome==control::Applied);
    Station station;station.configure(c.snapshot().on,c.snapshot().off,c.snapshot().period);
    assert(station.period==900&&station.activePeriod==300);
    for(int i=0;i<3;++i)station.tick(14,0);
    assert(station.status&WIND);
    cmd.id[0]=3;cmd.method=control::StopIrrigation;
    assert(c.execute(cmd,200,true,true,5).outcome==control::Expired);
    assert(c.execute(cmd,150,true,false,5).outcome==control::Applied);
    control::Controller<Memory> reboot(flash);assert(reboot.begin());
    assert(reboot.snapshot().stop&&reboot.snapshot().version==2);
    assert(reboot.execute(cmd,151,true,true,6).outcome==control::Unknown);
    assert(!reboot.confirm(false)&&reboot.confirm(true));
    control::Controller<Memory> again(flash);assert(again.begin()&&!again.snapshot().stop);
    wire::Payload p;p.byte(10);p.byte(16);for(auto b:cmd.id)p.byte(b);
    p.field(2,100);p.field(3,200);p.field(4,2);
    control::Command decoded;assert(control::decode(p,decoded)&&decoded.method==2);
    p.field(4,2);assert(!control::decode(p,decoded));
    const auto result=control::encode(1,150,cmd,{control::Applied,control::Ok},again.snapshot(),900,false,true);
    assert(result.ok&&result.size<=51);
    std::cout<<"Command validation, durable admission, replay, safety and configuration PASS\n";
}
