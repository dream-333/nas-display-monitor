#pragma once
#include "protocol.h"
// Only native USB may call this decoder. UDP always goes through token validation.
inline bool decodeUsbPacket(const char *buffer,size_t length,Metrics &result,uint32_t &sequence) {
  if(length>1024)return false;
  StaticJsonDocument<3072> doc;
  if(deserializeJson(doc,buffer,length))return false;
  JsonObjectConst o=doc.as<JsonObjectConst>();
  if(!o["v"].is<int>()||o["v"].as<int>()!=1||!o["kind"].is<const char*>()||
     strcmp(o["kind"].as<const char*>(),"nas-display-sample")||!o["seq"].is<uint32_t>())return false;
  Metrics next;
  if(!decodeMetrics(o,next))return false;
  result=next;sequence=o["seq"].as<uint32_t>();return true;
}
struct UsbLineBuffer {
  char buffer[1025];size_t used=0;bool dropping=false;
  bool feed(char c){
    if(c=='\r')return false;
    if(c=='\n'){
      bool ready=!dropping&&used>0;
      buffer[used]=0;used=0;dropping=false;return ready;
    }
    if(!dropping){if(used<1024)buffer[used++]=c;else dropping=true;}
    return false;
  }
};
