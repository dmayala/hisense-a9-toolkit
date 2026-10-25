/* SPDX-License-Identifier: Apache-2.0
 * Cancel the dead instance's bind timers before reusing a persistent record.
 * dexlib2 relocates branch targets, exception ranges and debug locations.
 */
import java.io.*;
import java.util.*;
import org.jf.dexlib2.*;
import org.jf.dexlib2.iface.*;
import org.jf.dexlib2.iface.instruction.*;
import org.jf.dexlib2.iface.reference.*;
import org.jf.dexlib2.builder.*;
import org.jf.dexlib2.builder.instruction.*;
import org.jf.dexlib2.immutable.*;
import org.jf.dexlib2.rewriter.*;
import org.jf.dexlib2.writer.pool.DexPool;

public class PatchServices {
 static final String AMS="Lcom/android/server/am/ActivityManagerService;";
 static final String REMOVE="Landroid/os/Handler;->removeMessages(ILjava/lang/Object;)V";
 static int changed;
 static void require(boolean ok,String reason) {
  if(!ok) throw new IllegalArgumentException(reason);
 }
 static boolean target(Method m) {
  return m.getDefiningClass().equals(AMS) && m.getName().equals("cleanUpApplicationRecordLocked")
   && m.getParameterTypes().toString().equals("[Lcom/android/server/am/ProcessRecord;, I, Z, Z, I, Z, Z]");
 }
 static Method patch(Method m) {
  MutableMethodImplementation body=new MutableMethodImplementation(m.getImplementation());
  List<BuilderInstruction> code=body.getInstructions();
  int at=-1,provider=0,soft=0,hard=0;
  for(int n=2;n<code.size();n++) {
   Instruction call=code.get(n),constant=code.get(n-1),load=code.get(n-2);
   if(!(call instanceof ReferenceInstruction) || !((ReferenceInstruction)call).getReference().toString().equals(REMOVE))continue;
   require(constant.getOpcode()==Opcode.CONST_16 && load.getOpcode()==Opcode.IGET_OBJECT,"unexpected handler sequence");
   int message=((NarrowLiteralInstruction)constant).getNarrowLiteral();
   if(message==57) {provider++;at=n-2;}
   if(message==82)soft++;
   if(message==83)hard++;
  }
  require(provider==1 && soft==1 && hard==1,"unsupported or already patched bind cleanup");
  BuilderInstruction22c get=(BuilderInstruction22c)code.get(at);
  BuilderInstruction21s value=(BuilderInstruction21s)code.get(at+1);
  BuilderInstruction35c call=(BuilderInstruction35c)code.get(at+2);
  require(get.getReference().toString().equals(AMS+"->mHandler:Lcom/android/server/am/ActivityManagerService$MainHandler;"),"not the AMS handler");
  require(call.getRegisterCount()==3 && call.getRegisterC()==get.getRegisterA()
   && call.getRegisterD()==value.getRegisterA(),"handler argument mismatch");
  List<BuilderInstruction> added=new ArrayList<>();
  for(int message:new int[]{82,83}) {
   added.add(new BuilderInstruction22c(Opcode.IGET_OBJECT,get.getRegisterA(),get.getRegisterB(),get.getReference()));
   added.add(new BuilderInstruction21s(Opcode.CONST_16,value.getRegisterA(),message));
   added.add(new BuilderInstruction35c(Opcode.INVOKE_VIRTUAL,3,call.getRegisterC(),call.getRegisterD(),call.getRegisterE(),0,0,call.getReference()));
  }
  added.add(new BuilderInstruction22c(Opcode.IGET_OBJECT,get.getRegisterA(),get.getRegisterB(),get.getReference()));
  // Replacing at the old label makes branches into this block run the clears.
  // Inserting before that label would leave those branches skipping the fix.
  body.replaceInstruction(at,added.get(0));
  for(int n=1;n<added.size();n++)body.addInstruction(at+n,added.get(n));
  changed++;
  return new ImmutableMethod(m.getDefiningClass(),m.getName(),m.getParameters(),m.getReturnType(),
   m.getAccessFlags(),m.getAnnotations(),m.getHiddenApiRestrictions(),body);
 }
 public static void main(String[] args) throws Exception {
  MultiDexContainer<? extends DexFile> container=DexFileFactory.loadDexContainer(new File(args[0]),Opcodes.getDefault());
  DexFile dex=container.getEntry("classes.dex").getDexFile();
  DexFile rewritten=new DexRewriter(new RewriterModule() {
   @Override public Rewriter<Method> getMethodRewriter(Rewriters rewriters) {
    return method -> target(method) ? patch(method) : method;
   }
  }).getDexFileRewriter().rewrite(dex);
  DexPool.writeTo(args[1],rewritten);
  require(changed==1,"expected exactly one cleanup method");
  System.out.println("PATCHED persistent_restart soft=82 hard=83 provider=57");
 }
}
