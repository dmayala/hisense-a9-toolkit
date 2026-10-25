/* Executes only isolated production function bytes with synthetic objects.
 * No HAL main, Binder, ALSA, mixer, PCM, device, or sound calls. */
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
static unsigned char *image;
static unsigned long offset(unsigned long va) {return va;} /* donor LOAD offsets checked by builder */
static void load_range(uintptr_t va,size_t n) {memcpy((void*)va,image+offset(va),n);}
static void *ok(void) {return (void*)1;}
static void *exception(int n) {return (void*)(intptr_t)n;}
static int quiet(void) {return 0;}
static void stub(uintptr_t va,void *fn) {
 uint32_t code[]={0x58000050,0xd61f0200};memcpy((void*)va,code,8);memcpy((void*)(va+8),&fn,8);
}
extern uintptr_t call_status(void*);
extern uintptr_t call_select(void*,void*,uint64_t);
__asm__(
".text\n.global call_status\ncall_status:\n"
" sub sp,sp,#32\n str x30,[sp,#16]\n mov x1,x0\n mov x0,xzr\n mov x8,sp\n"
" mov x16,#0x2320\n movk x16,#7,lsl#16\n blr x16\n ldr x0,[sp]\n ldr x30,[sp,#16]\n add sp,sp,#32\n ret\n"
".global call_select\ncall_select:\n"
" stp x20,x21,[sp,#-48]!\n stp x22,x23,[sp,#16]\n str x30,[sp,#32]\n"
" mov x20,x0\n mov x21,x1\n mov x23,x2\n mov x22,#0x1234\n mov x0,x22\n"
" mov x16,#0x7678\n movk x16,#12,lsl#16\n blr x16\n"
" ldr x30,[sp,#32]\n ldp x22,x23,[sp,#16]\n ldp x20,x21,[sp],#48\n ret\n");
static void integer(void*p,size_t at,unsigned n) {memcpy((char*)p+at,&n,4);}
static void word(void*p,size_t at,uintptr_t n) {memcpy((char*)p+at,&n,8);}
static void desc(unsigned char *d,unsigned type,const char*conn,int heap) {
 memset(d,0,64);integer(d,0,type);size_t n=strlen(conn);
 if(heap) {d[8]=1;word(d,16,n);word(d,24,(uintptr_t)conn);}
 else {d[8]=(unsigned char)(n*2);memcpy(d+9,conn,n);}
}
int main(int argc,char**argv) {
 assert(argc==3);FILE*f=fopen(argv[1],"rb");assert(f);fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);image=malloc(n);assert(image&&fread(image,1,n,f)==(size_t)n);fclose(f);
 /* Fixed mapping intentionally contains only two functions and status stubs. */
 for(uintptr_t va=0x72000;va<=0x72000;va+=4096)assert(mmap((void*)va,4096,7,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0)==(void*)va);
 assert(mmap((void*)0xc7000,4096,7,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0)==(void*)0xc7000);
 assert(mmap((void*)0x10a000,8192,7,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0)==(void*)0x10a000);
 load_range(0x72320,664);load_range(0xc7678,4);*(uint32_t*)0xc767c=0xd65f03c0;
 stub(0x10b120,ok);stub(0x10b270,exception);stub(0x10add0,quiet);
 __builtin___clear_cache((char*)0x72000,(char*)0x73000);__builtin___clear_cache((char*)0xc7000,(char*)0xc8000);__builtin___clear_cache((char*)0x10a000,(char*)0x10c000);
 int patched=atoi(argv[2]);unsigned char port[256]={0},saved[256];integer(port,0xe8,1);word(port,0x20,0x1000);word(port,0x28,0x1070);
 for(unsigned type=136;type<=137;type++)for(int heap=0;heap<2;heap++){
  desc(port+0x70,type,"analog",heap);memcpy(saved,port,sizeof(port));
  assert((intptr_t)call_status(port)==(patched?1:-5));assert(!memcmp(saved,port,sizeof(port)));
 }
 puts("PASS real donor port function: analog headphone/headset connection, inline/heap strings; object unchanged");
 desc(port+0x70,140,"",0);assert(call_status(port)==1);
 desc(port+0x70,136,"usb",0);assert((intptr_t)call_status(port)==-5);
 desc(port+0x70,7,"analog",0);assert((intptr_t)call_status(port)==-5);
 desc(port+0x70,136,"analog",0);word(port,0x28,0x1000);assert((intptr_t)call_status(port)==-5);
 integer(port,0xe8,0);assert((intptr_t)call_status(port)==-3);
 puts("PASS built-ins, unrelated connections and original exception codes retained; no invented mic endpoint");
 unsigned char device[64],stream[512]={0};uintptr_t vector[3]={(uintptr_t)device,(uintptr_t)device+64,(uintptr_t)device+64};
 for(unsigned type=136;type<=137;type++)for(int heap=0;heap<2;heap++){
  desc(device,type,"analog",heap);memcpy(saved,device,64);
  assert(call_select(vector,stream,0)==0x1234);uint64_t pair;memcpy(&pair,stream+0x1c0,8);
  assert(pair==(patched?UINT64_C(25)<<32:0));assert(!memcmp(saved,device,64));
 }
 const uint64_t old=UINT64_C(24)<<32;
 desc(device,141,"",0);assert(call_select(vector,stream,old)==0x1234);assert(!memcmp(stream+0x1c0,&old,8));
 desc(device,137,"analog",0);stream[0x84]=1;call_select(vector,stream,old);assert(!memcmp(stream+0x1c0,&old,8));stream[0x84]=0;
 uint64_t ignored=UINT64_MAX;call_select(vector,stream,ignored);assert(!memcmp(stream+0x1c0,&ignored,8));
 vector[1]=vector[0];call_select(vector,stream,old);assert(!memcmp(stream+0x1c0,&old,8));
 puts("PASS actual backend-store branch: PCM25 only for wired outputs; earpiece/input/ignored/disconnect preserved");
 return 0;
}
