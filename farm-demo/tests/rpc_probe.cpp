// Native executable for cross-language protocol/integration tests, not Wokwi RF.
#include "commands.hpp"
#include <cstdlib>
#include <iostream>
#include <string>
struct Memory{
    uint8_t data[4096];
    Memory(){memset(data,255,sizeof(data));}
    bool commandRead(unsigned at,uint8_t* b,unsigned n){memcpy(b,data+at,n);return true;}
    bool commandProgram(unsigned at,const uint8_t* b,unsigned n){
        for(unsigned i=0;i<n;++i){if((data[at+i]&b[i])!=b[i])return false;data[at+i]&=b[i];}
        return true;
    }
};
int main(int argc,char** argv){
    if(argc!=5)return 2;
    farm::wire::Payload p;std::string hex=argv[1];
    if(hex.size()%2)return 2;
    for(size_t i=0;i<hex.size();i+=2)if(!p.byte(uint8_t(std::stoul(hex.substr(i,2),nullptr,16))))return 2;
    farm::control::Command cmd;if(!farm::control::decode(p,cmd))return 3;
    Memory m;farm::control::Controller<Memory> c(m);if(!c.begin())return 4;
    uint32_t now=std::stoul(argv[2]),seq=std::stoul(argv[3]);
    auto result=c.execute(cmd,now,true,std::string(argv[4])=="safe",1);
    auto out=farm::control::encode(seq,now,cmd,result,c.snapshot(),c.snapshot().period,c.testing(1),c.snapshot().stop);
    const char* digits="0123456789abcdef";
    for(size_t i=0;i<out.size;++i)std::cout<<digits[out.data[i]>>4]<<digits[out.data[i]&15];
    std::cout<<"\n";
}
