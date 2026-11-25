#pragma once
#include "wire.hpp"
#include <string.h>

namespace farm::storage {
// STM32L4 geometry: two metadata pages and thirty data pages.
// A backend implements read, aligned program (erased doublewords), erasePage.
constexpr unsigned bytes=65536, page=2048, metadata=4096, slot=64, slots=960;
constexpr unsigned per_page=page/slot, event_reserve=per_page;
static_assert(metadata+slots*slot==bytes);
inline uint32_t load32(const uint8_t* p){
    return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);
}
inline void store32(uint8_t* p,uint32_t n){
    for(unsigned i=0;i<4;++i)p[i]=uint8_t(n>>(8*i));
}
inline bool filled(const uint8_t* p,unsigned n,uint8_t v){
    for(unsigned i=0;i<n;++i)if(p[i]!=v)return false;
    return true;
}
inline uint32_t crc(const uint8_t* p,unsigned n,uint32_t c=0xffffffffu){
    for(unsigned i=0;i<n;++i){
        c^=p[i];
        for(unsigned b=0;b<8;++b)c=(c>>1)^((c&1)?0xedb88320u:0u);
    }
    return c;
}
struct Frame{
    uint32_t sequence=0;
    uint8_t port=0;
    wire::Payload payload{};
};
enum class StoreResult { Stored, Full, Invalid, IoError };
template<class Flash> class Ring {
    Flash& flash;
    // Port is pending state; 0 erased candidate, 1 occupied/dead/acknowledged.
    uint8_t ports[slots]={}, lengths[slots]={};
    uint32_t sequences[slots]={};
    bool sent[slots]={};
    unsigned cursor=0, metaPage=0, metaNext=0;
    bool healthy=false, warning=false;
    static constexpr unsigned metaEntries=page/8;
    static unsigned address(unsigned i){return metadata+i*slot;}
    static uint32_t checksum(const uint8_t* b){
        return ~crc(b+8,48,crc(b,4));
    }
    static bool unpack(const uint8_t* b,Frame& out){
        out=Frame{};
        out.sequence=load32(b);
        out.port=b[8];
        const unsigned tail=b[9];
        if(!out.sequence || (out.port!=wire::PERIODIC_PORT&&out.port!=wire::EVENT_PORT) ||
           tail>46 || load32(b+4)!=checksum(b))return false;
        out.payload.field(1,out.sequence);
        for(unsigned i=0;i<tail;++i)out.payload.byte(b[10+i]);
        return out.payload.ok;
    }
    void updateWarning(){
        if(count*100>=slots*80)warning=true;
        else if(count*100<slots*70)warning=false;
    }
    bool fail(){healthy=false;return false;}
    bool recoverMetadata(){
        uint8_t b[128];
        unsigned ends[2]={};
        for(unsigned m=0;m<2;++m){
            for(unsigned off=0;off<page;off+=sizeof(b)){
                if(!flash.read(m*page+off,b,sizeof(b)))return false;
                for(unsigned j=0;j<sizeof(b);j+=8){
                    if(filled(b+j,8,0xff))continue;
                    ends[m]=(off+j)/8+1;
                    const auto n=load32(b+j);
                    if(n && load32(b+j+4)==~n && n>highwater){highwater=n;metaPage=m;}
                }
            }
        }
        metaNext=ends[metaPage];
        // A non-erased, invalid metadata journal with no recoverable counter is
        // unsafe to initialize as a fresh station (sequence reuse).
        if(!highwater&&(ends[0]||ends[1]))return false;
        return true;
    }
    bool reclaim(unsigned& index){
        for(unsigned n=0;n<slots/per_page;++n){
            const unsigned first=((cursor/per_page+n)%(slots/per_page))*per_page;
            bool pending=false;
            for(unsigned j=first;j<first+per_page;++j)if(ports[j]>=10)pending=true;
            if(pending)continue;
            if(!flash.erasePage(address(first)))return fail();
            for(unsigned j=first;j<first+per_page;++j){
                ports[j]=0;lengths[j]=0;sequences[j]=0;sent[j]=false;
            }
            index=first;return true;
        }
        return false;
    }
public:
    uint32_t highwater=0;
    unsigned count=0,corrupt=0;
    explicit Ring(Flash& f):flash(f){}
    bool ready() const{return healthy;}
    bool nearFull() const{return warning;}
    bool begin(){
        healthy=false;highwater=0;count=0;corrupt=0;cursor=0;metaPage=0;metaNext=0;warning=false;
        memset(ports,0,sizeof(ports));memset(lengths,0,sizeof(lengths));
        memset(sequences,0,sizeof(sequences));memset(sent,0,sizeof(sent));
        if(!recoverMetadata())return false;
        uint8_t b[slot];
        for(unsigned i=0;i<slots;++i){
            if(!flash.read(address(i),b,8))return false;
            if(filled(b,8,0xff))continue; // body checked before reuse (torn write)
            if(!flash.read(address(i)+8,b+8,56))return false;
            ports[i]=1;
            Frame f;
            if(!unpack(b,f)||f.sequence>highwater){++corrupt;continue;}
            if(filled(b+56,8,0))continue; // explicit application ACK persisted
            // Nonblank partial ACK is not interpreted as a valid ACK.
            if(!filled(b+56,8,0xff)){++corrupt;return false;}
            for(unsigned j=0;j<i;++j)if(ports[j]>=10&&sequences[j]==f.sequence)return false;
            ports[i]=f.port;lengths[i]=uint8_t(f.payload.size);sequences[i]=f.sequence;++count;
        }
        updateWarning();healthy=true;return true;
    }
    // Reserve durably BEFORE assigning a new application identity. A crash
    // between reservation and slot commit can cause a detectable sequence gap,
    // never reuse. The old journal page remains valid during rollover.
    uint32_t reserve(){
        if(!healthy||highwater==UINT32_MAX)return 0;
        if(metaNext==metaEntries){
            const unsigned other=1-metaPage;
            if(!flash.erasePage(other*page)){fail();return 0;}
            metaPage=other;metaNext=0;
        }
        const uint32_t next=highwater+1;
        uint8_t b[8],verify[8];store32(b,next);store32(b+4,~next);
        const unsigned at=metaPage*page+8*metaNext;
        if(!flash.program(at,b,8)||!flash.read(at,verify,8)||memcmp(b,verify,8)){
            fail();return 0;
        }
        ++metaNext;highwater=next;return next;
    }
    StoreResult append(const Frame& f){
        if(!healthy)return StoreResult::IoError;
        if(!f.sequence||f.sequence!=highwater||!f.payload.ok||
           (f.port!=wire::PERIODIC_PORT&&f.port!=wire::EVENT_PORT))return StoreResult::Invalid;
        for(unsigned i=0;i<slots;++i)if(ports[i]>=10&&sequences[i]==f.sequence)return StoreResult::Invalid;
        wire::Payload prefix;prefix.field(1,f.sequence);
        if(f.payload.size<prefix.size||f.payload.size>wire::capacity||
           f.payload.size-prefix.size>46||memcmp(f.payload.data,prefix.data,prefix.size))return StoreResult::Invalid;
        if(count==slots||(f.port==wire::PERIODIC_PORT&&count>=slots-event_reserve))return StoreResult::Full;
        unsigned index=slots;
        uint8_t b[slot];
        for(unsigned n=0;n<slots;++n){
            const unsigned i=(cursor+n)%slots;
            if(ports[i])continue;
            if(!flash.read(address(i),b,slot)){fail();return StoreResult::IoError;}
            if(!filled(b,slot,0xff)){ports[i]=1;++corrupt;continue;}
            index=i;break;
        }
        if(index==slots&&!reclaim(index))return healthy?StoreResult::Full:StoreResult::IoError;
        memset(b,0xff,sizeof(b));
        store32(b,f.sequence);b[8]=f.port;b[9]=uint8_t(f.payload.size-prefix.size);
        memcpy(b+10,f.payload.data+prefix.size,b[9]);
        store32(b+4,checksum(b));
        const auto at=address(index);
        // Body first; commit header last. ACK has its own erased doubleword.
        uint8_t check[slot];
        if(!flash.program(at+8,b+8,48)||!flash.program(at,b,8)||
           !flash.read(at,check,slot)||memcmp(b,check,slot)){
            fail();return StoreResult::IoError;
        }
        ports[index]=f.port;sequences[index]=f.sequence;lengths[index]=uint8_t(f.payload.size);
        sent[index]=false;cursor=(index+1)%slots;++count;updateWarning();
        return StoreResult::Stored;
    }
    // Prefer events but alternate with periodic and between newest and oldest
    // to prevent starvation during recovery. Oversize stays pending.
    bool select(unsigned maxBytes,bool eventFirst,bool newest,Frame& out){
        if(!healthy)return false;
        unsigned pick=slots;
        for(unsigned pass=0;pass<2&&pick==slots;++pass){
            const uint8_t wanted=(eventFirst!=(pass!=0))?wire::EVENT_PORT:wire::PERIODIC_PORT;
            for(unsigned i=0;i<slots;++i){
                if(ports[i]!=wanted||lengths[i]>maxBytes)continue;
                if(pick==slots||(newest?sequences[i]>sequences[pick]:sequences[i]<sequences[pick]))pick=i;
            }
        }
        if(pick==slots)return false;
        uint8_t b[slot];
        if(!flash.read(address(pick),b,slot)){fail();return false;}
        if(!unpack(b,out)||out.sequence!=sequences[pick]||out.port!=ports[pick]||out.payload.size!=lengths[pick]){
            ++corrupt;fail();return false;
        }
        return true;
    }
    void markSent(uint32_t seq){
        for(unsigned i=0;i<slots;++i)if(ports[i]>=10&&sequences[i]==seq)sent[i]=true;
    }
    bool acknowledge(uint32_t seq){
        if(!healthy)return false;
        for(unsigned i=0;i<slots;++i){
            if(ports[i]<10||sequences[i]!=seq||!sent[i])continue;
            uint8_t zero[8]={},check[8];
            if(!flash.program(address(i)+56,zero,8)||!flash.read(address(i)+56,check,8)||!filled(check,8,0))return fail();
            ports[i]=1;sent[i]=false;--count;updateWarning();return true;
        }
        return false;
    }
};
} // namespace farm::storage
