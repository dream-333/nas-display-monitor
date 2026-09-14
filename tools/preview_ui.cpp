// Host harness for the actual dashboard drawing and interaction code.
// Dashboard glyphs and drawing operations are the same pixels used by the firmware.
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include "ui_state.h"
using std::isfinite;
struct String:std::string{
 using std::string::string;
 String(const std::string&s):std::string(s){}
 String(int v):std::string(std::to_string(v)){}
 String(unsigned short v):String((int)v){}
 String(float v,int n=1){std::ostringstream s;s<<std::fixed<<std::setprecision(n)<<v;assign(s.str());}
};
std::ostream *out=nullptr;
uint32_t tick=120000;uint32_t millis(){return tick;}
constexpr int TL_DATUM=0,TR_DATUM=2,TC_DATUM=1;
constexpr uint16_t FG=0xffff,MUTED=0x9cf3,CYAN=0x36dd,PURPLE=0xb39f,GREEN=0x5e95,PANEL=0x10c3,TRACK=0x2126,TFT_ORANGE=0xfd20;
const uint32_t STALE_MS=10000;
float constrain(float v,float l,float h){return std::max(l,std::min(h,v));}
std::string color(uint16_t v){char b[8];snprintf(b,8,"#%02x%02x%02x",((v>>11)&31)*255/31,((v>>5)&63)*255/63,(v&31)*255/31);return b;}
std::string escape(const String&s){std::string r;for(char c:s){if(c=='&')r+="&amp;";else if(c=='<')r+="&lt;";else r+=c;}return r;}
struct Canvas{
 int size=1;
 void rect(int x,int y,int w,int h,int radius,uint16_t c,bool fill){assert(x>=0&&y>=0&&w>=0&&h>=0&&x+w<=536&&y+h<=240);if(out)*out<<"<rect x='"<<x<<"' y='"<<y<<"' width='"<<w<<"' height='"<<h<<"' rx='"<<radius<<"' fill='"<<(fill?color(c):"none")<<"' stroke='"<<color(c)<<"' stroke-width='"<<(fill?0:1)<<"'/>";}
 void fillRect(int x,int y,int w,int h,uint16_t c){rect(x,y,w,h,0,c,true);}
 void drawRect(int x,int y,int w,int h,uint16_t c){rect(x,y,w,h,0,c,false);}
 void drawCircle(int x,int y,int r,uint16_t c){assert(x-r>=0&&y-r>=0&&x+r<536&&y+r<240);if(out)*out<<"<circle cx='"<<x<<"' cy='"<<y<<"' r='"<<r<<"' fill='none' stroke='"<<color(c)<<"'/>";}
 void fillRoundRect(int x,int y,int w,int h,int r,uint16_t c){rect(x,y,w,h,r,c,true);}
 void drawLine(int x,int y,int xx,int yy,uint16_t c){assert(x>=0&&x<536&&xx>=0&&xx<536&&y>=0&&y<240&&yy>=0&&yy<240);if(out)*out<<"<path d='M"<<x<<","<<y<<"L"<<xx<<","<<yy<<"' stroke='"<<color(c)<<"'/>";}
 void drawFastHLine(int x,int y,int w,uint16_t c){drawLine(x,y,x+w,y,c);}
 void drawFastVLine(int x,int y,int h,uint16_t c){drawLine(x,y,x,y+h,c);}
 void setTextSize(int n){size=n;}
 int textWidth(const String&s,int font){return s.size()*(font==4?13:8)*size;}
}canvas;
void textAt(int x,int y,const String&s,uint16_t c=FG,int font=2,int size=1,int datum=TL_DATUM){if(out)*out<<"<text x='"<<x<<"' y='"<<y<<"' dominant-baseline='text-before-edge' text-anchor='"<<(datum==TR_DATUM?"end":datum==TC_DATUM?"middle":"start")<<"' font-family='sans-serif' font-size='"<<(font==4?24:font==2?16:8)*size<<"' fill='"<<color(c)<<"'>"<<escape(s)<<"</text>";}
void header(const String&t,const String&s,uint16_t c){textAt(12,8,t,FG,4);textAt(524,8,s,c,2,1,TR_DATUM);}
String value(float v,const char*u="",int n=1){return isfinite(v)?String(v,n)+u:String("--");}
String rate(float b){return !isfinite(b)?String("--"):b>=1048576?String(b/1048576,1)+" MiB/s":String(b/1024,1)+" KiB/s";}
struct DiskMetric {char name[17];char state[6];float temperature;};
struct Metrics{float cpu,ct,mem,gpu,gt,d1,d2,rx,tx;float st=48,dh=46,f1=2089,f2=580;unsigned short diskStart=0,diskTotal=6;unsigned char diskCount=4;DiskMetric disks[4]={{"NVMe nvme0n1","ok",41},{"NVMe nvme1n1","ok",46},{"SATA sda","ok",35},{"SATA sdb","sleep",NAN}};}stats={2,47,30,0,44,41,46,1638,3891};
bool haveData=true,setupMode=false,infoPage=false,usbData=false;uint32_t receivedAt=120000;
constexpr int WL_CONNECTED=3;
struct Wifi{int state=3;int status(){return state;}struct IP{String toString(){return "192.168.1.50";}};IP localIP(){return {};}}WiFi;
struct Prefs{int removes=0;void putUChar(const char*,uint8_t){}void remove(const char*){removes++;}}prefs;
struct Esp{bool restarted=false;void restart(){restarted=true;}}ESP;
void lcd_setBrightness(uint8_t){}
#include "dashboard.h"
int main(int argc,char**argv){
 assert(uiWidth("100",42)+uiWidth("%",20)<190);
 assert(uiWidth("100000",30)+uiWidth("RPM",20)+8<226);
 assert(uiWidth(uiFit("WWWWWWWWWWWWWWWW",20,226),20)<=226);
 // All labels/numerals we render have real glyphs, including drawn Celsius C.
 for(int size:{14,16,20,24,30,42,78})for(char ch:std::string("0123456789-C"))assert(uiGlyph(size,ch));
 // Exercise production interaction logic without hardware or secrets.
 handleKey(0,KeyEvent::Short);assert(page==1);
 handleKey(1,KeyEvent::Short);assert(skin==1);
 handleKey(0,KeyEvent::Long);assert(menuOpen);
 menuRow=3;handleKey(1,KeyEvent::Short);assert(resetConfirm&&!ESP.restarted);
 tick+=5001;handleKey(1,KeyEvent::Short);assert(!ESP.restarted);
 handleKey(0,KeyEvent::Short);assert(!resetConfirm);
 menuRow=3;handleKey(1,KeyEvent::Short);handleKey(1,KeyEvent::Short);assert(ESP.restarted&&prefs.removes==3);
 menuOpen=false;setupMode=true;usbData=true;page=0;handleKey(0,KeyEvent::Short);assert(page==1);setupMode=false;usbData=false;screenOff=true;page=0;handleKey(0,KeyEvent::Short);assert(!screenOff&&wakeGuard&&page==0);
 handleKey(1,KeyEvent::Short);assert(skin==1);wakeGuard=false;
 receivedAt=tick;assert(freshData());WiFi.state=0;assert(!freshData());usbData=true;assert(freshData());usbData=false;WiFi.state=3;receivedAt=tick-STALE_MS;assert(!freshData());receivedAt=tick;
 usbData=true;
 for(int i=0;i<60;i++){float v[]={float(50+10*sin(i*.2)),float(45+5*cos(i*.3)),42,float(2e6+1e6*sin(i*.15)),float(5e5+2e5*cos(i*.3)),41};history.push(v);}
 std::ofstream file(argc>1?argv[1]:"/tmp/nas-ui-preview.html");out=&file;
 file<<"<!doctype html><meta charset='utf-8'><title>NAS UI5.1 preview</title><style>body{background:#131820;color:#edf1fa;font:16px sans-serif;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(540px,1fr));gap:20px}svg{width:100%;max-width:804px;background:black;border:1px solid #555}h2{font-size:18px}</style><h1>NAS Display / UI5.1</h1><p>Production drawing commands and embedded glyph pixels. Demonstration data, not live readings.</p><main>";
 for(int s=0;s<3;s++)for(int p=0;p<4;p++){skin=s;page=p;file<<"<section><h2>"<<skinNames[s]<<" / "<<pageNames[p]<<"</h2><svg viewBox='0 0 536 240'>";dashboard();file<<"</svg></section>";}
 haveData=false;skin=0;page=0;file<<"<section><h2>Missing data</h2><svg viewBox='0 0 536 240'>";dashboard();file<<"</svg></section>";
 haveData=true;stats.rx=stats.tx=1e12;page=2;file<<"<section><h2>Maximum protocol rates</h2><svg viewBox='0 0 536 240'>";dashboard();file<<"</svg></section>";
 stats.cpu=stats.gpu=stats.mem=100;stats.ct=150;stats.gt=-50;stats.f1=stats.f2=100000;stats.st=125;
 page=0;file<<"<section><h2>Metric limits</h2><svg viewBox='0 0 536 240'>";dashboard();file<<"</svg></section>";
 page=1;file<<"<section><h2>Fan RPM limits</h2><svg viewBox='0 0 536 240'>";dashboard();file<<"</svg></section>";
 haveData=false;file<<"<section><h2>Unavailable fans and temperatures</h2><svg viewBox='0 0 536 240'>";dashboard();file<<"</svg></section>";
 haveData=true;page=3;stats.diskStart=65531;stats.diskTotal=65535;
 for(auto &disk:stats.disks){strcpy(disk.name,"WWWWWWWWWWWWWWWW");strcpy(disk.state,"ok");disk.temperature=125;}
 file<<"<section><h2>Long disk names and counts</h2><svg viewBox='0 0 536 240'>";dashboard();file<<"</svg></section>";
 resetConfirm=false;menuRow=0;
 file<<"<section><h2>Settings</h2><svg viewBox='0 0 536 240'>";drawMenu();file<<"</svg></section></main>";
}
