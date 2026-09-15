#include "protocol.h"
#include <cassert>
#include <string>
#include <iostream>
int main(){
 std::string p=R"({"v":1,"cpu":5,"ct":54,"mem":25,"gpu":97,"gt":51,"d1":41,"d2":46,"rx":1000,"tx":null})";
 Metrics m{};assert(decodePacket(p.c_str(),p.size(),m));assert(m.gpu==97&&m.d1==41&&isnan(m.tx));
 for(const auto &legacy:{std::string("\"old-code\""),std::string("null"),std::string("123")}){
   auto old=p.substr(0,p.size()-1)+",\"token\":"+legacy+"}";
   assert(decodePacket(old.c_str(),old.size(),m));assert(m.gpu==97);
 }
 for(auto pair:{std::make_pair(std::string("\"gpu\":97"),std::string("\"gpu\":101")),
                 std::make_pair(std::string("\"gpu\":97"),std::string("\"gpu\":\"97\"")),
                 std::make_pair(std::string("\"gpu\":97,"),std::string("")),
                 std::make_pair(std::string("\"v\":1"),std::string("\"v\":2"))}){
   std::string bad=p;bad.replace(bad.find(pair.first),pair.first.size(),pair.second);
   assert(!decodePacket(bad.c_str(),bad.size(),m));assert(m.gpu==97);
 }
 assert(!decodePacket("{}",2,m));std::string huge(1025,' ');assert(!decodePacket(huge.c_str(),huge.size(),m));
 assert(!decodePacket(p.c_str(),p.size()-3,m));
 std::string extended=p.substr(0,p.size()-1)+R"(,"st":48,"dh":52,"f1":2089,"f2":580,"di":0,"dn":6,"ds":[{"n":"SATA sda","t":35,"s":"ok"},{"n":"SATA sdb","t":null,"s":"sleep"}]})";
 assert(decodePacket(extended.c_str(),extended.size(),m));assert(m.f1==2089&&m.f2==580);assert(m.st==48&&m.diskCount==2&&m.disks[0].temperature==35&&isnan(m.disks[1].temperature));
 for(auto pair:{std::make_pair(std::string("\"dn\":6"),std::string("\"dn\":1")),
                std::make_pair(std::string("\"st\":48"),std::string("\"st\":-33")),
                std::make_pair(std::string("\"s\":\"ok\""),std::string("\"s\":\"bad\""))}){
   auto bad=extended;bad.replace(bad.find(pair.first),pair.first.size(),pair.second);assert(!decodePacket(bad.c_str(),bad.size(),m));assert(m.st==48);
 }
 assert(decodePacket(p.c_str(),p.size(),m));assert(m.diskCount==0&&isnan(m.st)&&isnan(m.f1)&&isnan(m.f2)); // old sender clears extensions
 std::cout<<"PASS firmware parser: token-free/legacy, valid/null, bounds, types, missing fields, version, truncation, length\n";
}
