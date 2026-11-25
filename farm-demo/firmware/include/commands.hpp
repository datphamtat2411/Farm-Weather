#pragma once
#include "flash_ring.hpp"
#include "weather.hpp"

namespace farm::control {
constexpr uint8_t result_port=22;
enum Method:uint32_t { TestBuzzer=1, StopIrrigation=2, SetConfig=3 };
enum Outcome:uint32_t { Applied=1, Rejected=2, Expired=3, Unknown=4 };
enum Reason:uint32_t { Ok=0, Invalid=1, Unsafe=2, Stale=3, Clock=4, Storage=5, Replay=6 };
struct Command {
    uint8_t id[16]={};
    uint32_t created=0,expires=0,method=0,version=0,on=0,off=0,period=0,ack=0;
};
inline bool varint(const wire::Payload& p,size_t& i,uint32_t& out){
    out=0;
    for(unsigned n=0;n<5&&i<p.size;++n){
        uint8_t b=p.data[i++];
        if(n==4&&b>15)return false;
        out|=uint32_t(b&127)<<(7*n);
        if(!(b&128))return n==0||b!=0;
    }
    return false;
}
inline bool decode(const wire::Payload& p,Command& c){
    c=Command{};
    if(!p.ok||p.size<18||p.data[0]!=10||p.data[1]!=16)return false;
    memcpy(c.id,p.data+2,16);
    if(storage::filled(c.id,16,0))return false;
    size_t i=18;unsigned fields=0,last=1;
    while(i<p.size){
        uint32_t tag=0,value=0;
        if(!varint(p,i,tag)||tag%8||tag/8<=last||tag/8>9||!varint(p,i,value))return false;
        last=tag/8;fields|=1u<<last;
        switch(last){
            case 2:c.created=value;break;case 3:c.expires=value;break;case 4:c.method=value;break;
            case 5:c.version=value;break;case 6:c.on=value;break;case 7:c.off=value;break;case 8:c.period=value;break;case 9:c.ack=value;break;
        }
    }
    return fields==(c.method==SetConfig?0x3fcu:0x21cu)&&c.ack&&c.created&&c.expires>c.created&&
        c.expires-c.created<=900&&c.method>=TestBuzzer&&c.method<=SetConfig;
}
struct Snapshot {uint32_t version=1,on=10,off=7,period=300;bool stop=false;};
inline bool valid(const Snapshot& s){return s.version>0&&s.version<=2147483647u&&s.off>0&&s.off<s.on&&s.on<=10&&wire::period(s.period);}
struct Result {Outcome outcome=Unknown;Reason reason=Storage;};
// Dedicated 4 KiB simulation configuration/replay journal, separate from the
// 64 KiB weather ring. Append-only; never silently evict command identity.
// Body first, CRC commit last. Torn writes disable remote control on reboot.
template<class Flash> class Controller {
    Flash& flash;
    static constexpr unsigned entry=48, count=4096/entry;
    unsigned next=0;
    bool healthy=false;
    uint32_t buzzerUntil=0;
    uint8_t ids[count][16]={};
    Snapshot state{};
    bool commit(const uint8_t* id,const Snapshot& s){
        if(!healthy||next>=count)return false;
        uint8_t b[entry]={},check[entry];
        memcpy(b+8,id,16);
        storage::store32(b+24,s.version);storage::store32(b+28,s.on);
        storage::store32(b+32,s.off);storage::store32(b+36,s.period);
        storage::store32(b+40,s.stop?1:0);
        storage::store32(b,0x434d4431u);storage::store32(b+4,~storage::crc(b+8,40));
        unsigned at=next*entry;
        if(!flash.commandProgram(at+8,b+8,40)||!flash.commandProgram(at,b,8)||
           !flash.commandRead(at,check,entry)||memcmp(b,check,entry)){
            healthy=false;return false;
        }
        memcpy(ids[next++],id,16);state=s;return true;
    }
public:
    explicit Controller(Flash& f):flash(f){}
    const Snapshot& snapshot()const{return state;}
    bool begin(){
        healthy=false;next=0;state=Snapshot{};buzzerUntil=0;
        uint8_t b[entry];
        for(unsigned i=0;i<count;++i){
            if(!flash.commandRead(i*entry,b,entry))return false;
            if(storage::filled(b,entry,255)){next=i;healthy=true;return true;}
            if(storage::load32(b)!=0x434d4431u||storage::load32(b+4)!=~storage::crc(b+8,40))return false;
            Snapshot s{storage::load32(b+24),storage::load32(b+28),storage::load32(b+32),
                       storage::load32(b+36),storage::load32(b+40)!=0};
            if(!valid(s)||storage::load32(b+40)>1)return false;
            memcpy(ids[i],b+8,16);state=s;
        }
        next=count;healthy=true;return true;
    }
    bool ready()const{return healthy&&next<count;}
    Result execute(const Command& c,uint32_t now,bool trusted,bool safe,uint32_t logical){
        if(!trusted)return {Rejected,Clock};
        if(now>=c.expires)return {Expired,Clock};
        if(now<c.created)return {Rejected,Clock};
        for(unsigned i=0;i<next;++i)if(!memcmp(ids[i],c.id,16))return {Unknown,Replay};
        if(!ready())return {Rejected,Storage};
        Snapshot wanted=state;
        if(c.method==SetConfig){
            wanted.version=c.version;wanted.on=c.on;wanted.off=c.off;wanted.period=c.period;
            Reason denied=Ok;
            if(!valid(wanted))denied=Invalid;
            else if(c.version<=state.version)denied=Stale;
            else if(!safe)denied=Unsafe;
            if(denied!=Ok){if(!commit(c.id,state))return {Rejected,Storage};return {Rejected,denied};}
        }else if(c.method==StopIrrigation)wanted.stop=true;
        else if(c.method!=TestBuzzer)return {Rejected,Invalid};
        // Durable admission BEFORE changing outputs. A crash after admission
        // but before confirmed result is UNKNOWN, never replay the side effect.
        if(!commit(c.id,wanted))return {Rejected,Storage};
        if(c.method==TestBuzzer)buzzerUntil=logical+3;
        return {Applied,Ok};
    }
    bool testing(uint32_t logical)const{return buzzerUntil&&int32_t(buzzerUntil-logical)>0;}
    bool confirm(bool safe){
        if(!safe||!state.stop)return false;
        Snapshot wanted=state;wanted.stop=false;uint8_t local[16]={};
        return commit(local,wanted);
    }
};
inline wire::Payload encode(uint32_t sequence,uint32_t ts,const Command& c,Result r,
                            const Snapshot& s,unsigned effective,bool buzzer,bool stop){
    wire::Payload p;
    p.field(1,sequence);p.field(2,ts);p.byte(26);p.byte(16);
    for(auto b:c.id)p.byte(b);
    p.field(4,r.outcome);p.field(5,r.reason);p.field(6,s.version);
    p.field(7,s.on);p.field(8,s.off);p.field(9,s.period);p.field(10,effective);
    p.field(11,unsigned(buzzer)|unsigned(stop)*2);
    return p;
}
}
