// Inspect a bounded data range, retaining addresses and decoded pointer strings.
// @category LightSpeedRadio
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import java.nio.file.*;
public class RadioMemory extends GhidraScript {
  String string(long value) {
    try { Address a=toAddr(value); StringBuilder s=new StringBuilder();
      for(int i=0;i<180;i++){int b=getByte(a.add(i))&255;if(b==0)return s.length()>1?s.toString():"";if(b<32||b>126)return "";s.append((char)b);}return "";
    }catch(Exception e){return "";}
  }
  public void run() throws Exception {
    String[] args=getScriptArgs(); StringBuilder out=new StringBuilder();
    for(int k=1;k+1<args.length;k+=2){Address start=toAddr(args[k]);int count=Integer.parseInt(args[k+1]);
      if(count<1||count>4096)throw new IllegalArgumentException("count");
      for(int i=0;i<count;i++){Address a=start.add(i*8);long v=getLong(a);Symbol sym=getSymbolAt(toAddr(v));
        out.append(a).append(" ").append(Long.toHexString(v)).append(" ").append(sym==null?"":sym.getName(true)).append(" ").append(string(v)).append("\n");
      }
    }
    Files.writeString(Paths.get(args[0]),out.toString());println("RADIO_MEMORY "+args[0]);
  }
}
