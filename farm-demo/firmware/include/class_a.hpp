#pragma once
#include "flash_ring.hpp"

namespace farm::link {
// Application ACK v1, FPort 20: repeated uint32 field 1, unpacked.
// Reject malformed, zero, duplicate, overlong, noncanonical and oversized ACKs.
constexpr unsigned max_acks=8;
struct Ack {uint32_t ids[max_acks]={};unsigned count=0;};
inline bool decodeAck(const wire::Payload& p,Ack& out){
    out=Ack{};
    if(!p.ok||!p.size||p.size>wire::capacity)return false;
    size_t i=0;
    while(i<p.size){
        if(out.count==max_acks||p.data[i++]!=8)return false;
        uint32_t value=0;unsigned shift=0,n=0;
        while(true){
            if(i==p.size||n==5)return false;
            const uint8_t b=p.data[i++];
            if(n==4&&b>15)return false;
            value|=uint32_t(b&127)<<shift;++n;
            if(!(b&128)){if(n>1&&b==0)return false;break;}
            shift+=7;
        }
        if(!value)return false;
        for(unsigned j=0;j<out.count;++j)if(out.ids[j]==value)return false;
        out.ids[out.count++]=value;
    }
    return true;
}
template<class Queue> class ClassA {
    Queue& queue;
    bool waiting=false;
    uint32_t txTime=0,nextTx=0;
    unsigned failures=0,turn=0;
    storage::Frame current{};
public:
    explicit ClassA(Queue& q):queue(q){}
    unsigned payloadLimit=51; // configurable simulation limit, not RF certification
    uint32_t frameCounter=0;
    uint32_t currentSequence()const{return current.sequence;}
    unsigned window(uint32_t now)const{
        if(!waiting)return 0;
        const uint32_t elapsed=now-txTime;
        return elapsed==1?1:elapsed==2?2:0;
    }
    // Logical seconds. One uplink in flight; application ACK only in RX1/RX2.
    // Bounded retry and recovery pacing. No blocking or sensor dependency.
    template<class Send> void tick(uint32_t now,Send send){
        if(waiting){
            if(now-txTime<=2)return;
            waiting=false;
            if(failures<5)++failures;
            const unsigned backoff=5u<<(failures-1);
            nextTx=now+(backoff>60?60:backoff);
        }
        if(int32_t(now-nextTx)<0||!queue.ready())return;
        if(!queue.select(payloadLimit,(turn&1)==0,(turn&2)!=0,current))return;
        ++turn;
        if(frameCounter==UINT32_MAX)return; // no simulated session counter wrap
        ++frameCounter;txTime=now;waiting=true;
        queue.markSent(current.sequence);
        send(current,frameCounter);
    }
    bool commandWindow(uint32_t now)const{return window(now)!=0;}
    unsigned downlink(uint32_t now,uint8_t port,const wire::Payload& p){
        if(port!=wire::ACK_PORT||!window(now))return 0;
        Ack a;
        if(!decodeAck(p,a))return 0;
        unsigned accepted=0;bool currentAck=false;
        for(unsigned i=0;i<a.count;++i){
            if(queue.acknowledge(a.ids[i])){
                ++accepted;
                if(a.ids[i]==current.sequence)currentAck=true;
            }
        }
        if(currentAck){waiting=false;failures=0;nextTx=now+5;}
        return accepted;
    }
};
} // namespace farm::link
