import java.io.*;
import org.jf.dexlib2.*;
import org.jf.dexlib2.iface.*;
import org.jf.dexlib2.iface.instruction.*;
public class DexMethod {
 public static void main(String[] args) throws Exception {
  MultiDexContainer<? extends DexFile> c=DexFileFactory.loadDexContainer(new File(args[0]),Opcodes.getDefault());
  for(String entry:c.getDexEntryNames()) for(ClassDef cl:c.getEntry(entry).getDexFile().getClasses()) {
   if(!cl.getType().equals(args[1]))continue;
   for(Method m:cl.getMethods()) if(m.getName().equals(args[2])) {
    System.out.println(entry+" "+cl.getType()+" "+m.getName()+m.getParameterTypes()+" -> "+m.getReturnType());
    int a=0;for(Instruction i:m.getImplementation().getInstructions()) {
     String s=i.getOpcode().name();
     if(i instanceof ReferenceInstruction) s+=" "+((ReferenceInstruction)i).getReference();
     if(i instanceof NarrowLiteralInstruction) s+=" literal="+((NarrowLiteralInstruction)i).getNarrowLiteral();
     if(i instanceof OffsetInstruction) s+=" target="+Integer.toHexString(a+((OffsetInstruction)i).getCodeOffset());
     if(i instanceof FiveRegisterInstruction) {FiveRegisterInstruction f=(FiveRegisterInstruction)i;s+=" regs="+f.getRegisterC()+","+f.getRegisterD()+","+f.getRegisterE()+","+f.getRegisterF()+","+f.getRegisterG()+" count="+f.getRegisterCount();}
     if(i instanceof ThreeRegisterInstruction) {ThreeRegisterInstruction t=(ThreeRegisterInstruction)i;s+=" regs="+t.getRegisterA()+","+t.getRegisterB()+","+t.getRegisterC();}
     else if(i instanceof TwoRegisterInstruction) {TwoRegisterInstruction t=(TwoRegisterInstruction)i;s+=" regs="+t.getRegisterA()+","+t.getRegisterB();}
     else if(i instanceof OneRegisterInstruction)s+=" reg="+((OneRegisterInstruction)i).getRegisterA();
     System.out.printf("%04x %s%n",a,s);a+=i.getCodeUnits();
    }
   }
  }
 }
}
