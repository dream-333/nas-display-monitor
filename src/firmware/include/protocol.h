#pragma once
#include <ArduinoJson.h>
#include <math.h>
#include <string.h>
struct DiskMetric { char name[17] = {}; char state[6] = {}; float temperature=NAN; };
struct Metrics {
  float cpu,ct,mem,gpu,gt,d1,d2,rx,tx;
  float st=NAN,dh=NAN,f1=NAN,f2=NAN;
  uint16_t diskStart=0,diskTotal=0; uint8_t diskCount=0;
  DiskMetric disks[4];
};
inline bool metricField(JsonObjectConst o,const char *key,float &out,float low,float high) {
  if(!o.containsKey(key)) return false;
  JsonVariantConst v=o[key]; if(v.isNull()){out=NAN;return true;}
  if(!v.is<float>()) return false;
  out=v.as<float>(); return isfinite(out)&&out>=low&&out<=high;
}
inline bool decodeMetrics(JsonObjectConst o,Metrics &result) {
  Metrics next;
  if(!metricField(o,"cpu",next.cpu,0,100)||!metricField(o,"ct",next.ct,-50,150)||!metricField(o,"mem",next.mem,0,100)||
     !metricField(o,"gpu",next.gpu,0,100)||!metricField(o,"gt",next.gt,-50,150)||!metricField(o,"d1",next.d1,-50,150)||
     !metricField(o,"d2",next.d2,-50,150)||!metricField(o,"rx",next.rx,0,1e12)||!metricField(o,"tx",next.tx,0,1e12))return false;
  const char *extra[]={"st","dh","f1","f2"};
  float *fields[]={&next.st,&next.dh,&next.f1,&next.f2};
  for(int i=0;i<4;i++)if(o.containsKey(extra[i])&&!metricField(o,extra[i],*fields[i],0,i<2?125:100000))return false;
  if(o.containsKey("ds")){
    if(!o["ds"].is<JsonArrayConst>()||!o["di"].is<uint16_t>()||!o["dn"].is<uint16_t>())return false;
    JsonArrayConst disks=o["ds"].as<JsonArrayConst>();if(disks.size()>4)return false;
    next.diskStart=o["di"];next.diskTotal=o["dn"];
    if((uint32_t)next.diskStart+disks.size()>next.diskTotal)return false;
    for(JsonObjectConst d:disks){
      if(d.isNull()||!d["n"].is<const char*>()||!d["s"].is<const char*>())return false;
      const char *name=d["n"],*state=d["s"];
      if(strlen(name)>16||strlen(state)>5)return false;
      for(const char *p=name;*p;p++)if((unsigned char)*p<32||(unsigned char)*p>126)return false;
      if(strcmp(state,"ok")&&strcmp(state,"sleep")&&strcmp(state,"old")&&strcmp(state,"wait")&&strcmp(state,"n/a"))return false;
      DiskMetric &target=next.disks[next.diskCount++];
      if(!metricField(d,"t",target.temperature,0,125))return false;
      if(strcmp(state,"ok"))target.temperature=NAN;
      strcpy(target.name,name);strcpy(target.state,state);
    }
  }
  result=next;return true;
}
inline bool decodePacket(const char *buffer,size_t length,const char *token,Metrics &result) {
  if(length>1024) return false;
  StaticJsonDocument<3072> doc;
  if(deserializeJson(doc,buffer,length))return false;
  JsonObjectConst o=doc.as<JsonObjectConst>();
  if(!o["v"].is<int>()||o["v"].as<int>()!=1||!o["token"].is<const char*>()||strcmp(token,o["token"].as<const char*>()))return false;
  Metrics next;
  if(!decodeMetrics(o,next))return false;
  result=next;
  return true;
}
