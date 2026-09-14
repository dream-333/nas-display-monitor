#include "usb_protocol.h"
#include <cassert>
#include <string>
#include <iostream>
int main(){
  const std::string p=R"({"kind":"nas-display-sample","v":1,"seq":123,"cpu":5,"ct":54,"mem":25,"gpu":97,"gt":51,"d1":41,"d2":46,"rx":1000,"tx":null})";
  Metrics m{};uint32_t seq=0;assert(decodeUsbPacket(p.c_str(),p.size(),m,seq));assert(seq==123&&m.gpu==97&&isnan(m.tx));
  assert(!decodePacket(p.c_str(),p.size(),"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",m)); // Never accept USB authentication bypass over UDP.
  for(auto pair:{std::make_pair(std::string("\"seq\":123"),std::string("\"seq\":-1")),
                 std::make_pair(std::string("\"seq\":123"),std::string("\"seq\":1.2")),
                 std::make_pair(std::string("\"gpu\":97"),std::string("\"gpu\":101")),
                 std::make_pair(std::string("\"gpu\":97,"),std::string("")),
                 std::make_pair(std::string("nas-display-sample"),std::string("unknown")),
                 std::make_pair(std::string("\"v\":1"),std::string("\"v\":2"))}){
    auto bad=p;bad.replace(bad.find(pair.first),pair.first.size(),pair.second);assert(!decodeUsbPacket(bad.c_str(),bad.size(),m,seq));assert(seq==123&&m.gpu==97);
  }
  UsbLineBuffer b;for(char c:p){assert(!b.feed(c));}
  assert(b.feed('\n'));assert(p==b.buffer);
  for(int i=0;i<1100;i++){assert(!b.feed('x'));}
  assert(!b.feed('\n'));
  for(char c:p){assert(!b.feed(c));}
  assert(!b.feed('\r'));assert(b.feed('\n'));assert(p==b.buffer);
  assert(!b.feed('\n'));assert(!decodeUsbPacket(p.c_str(),p.size()-3,m,seq));
  std::cout<<"PASS USB framing, resynchronization, bounds, ACK sequence and UDP isolation\n";
}
