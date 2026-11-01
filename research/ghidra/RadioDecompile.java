// Decompile explicitly selected functions, retaining inbound/outbound references.
// @category LightSpeedRadio
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import java.nio.file.*;
import java.util.*;

public class RadioDecompile extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Path root = Paths.get(args[0]); Files.createDirectories(root);
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try {
            for (int i = 1; i < args.length; i++) {
                String[] range = args[i].split(":");
                Address address = toAddr(range[0]);
                AddressSet bounded = null;
                if (range.length == 2) {
                    Address end = toAddr(range[1]);
                    bounded = new AddressSet(address, end.subtract(1));
                    Function old = getFunctionAt(address);
                    if (old != null) currentProgram.getFunctionManager().removeFunction(address);
                    clearListing(address, end.subtract(1));
                    new DisassembleCommand(address, bounded, true).applyTo(currentProgram, monitor);
                }
                Function f = getFunctionAt(address);
                if (f == null) f = getFunctionContaining(address);
                if (f == null) { disassemble(address); f = createFunction(address, null); }
                if (f == null) { println("NO_FUNCTION " + address); continue; }
                if (bounded != null) f.setBody(bounded);
                DecompileResults result = decompiler.decompileFunction(f, 45, monitor);
                StringBuilder text = new StringBuilder();
                text.append("// Program: ").append(currentProgram.getName()).append("\n");
                text.append("// SHA256: ").append(currentProgram.getExecutableSHA256()).append("\n");
                text.append("// Function: ").append(f.getName(true)).append(" @ ").append(f.getEntryPoint()).append("\n");
                ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(f.getEntryPoint());
                while (ri.hasNext()) { Reference r = ri.next(); text.append("// Inbound: ").append(r.getFromAddress()).append(" ").append(r.getReferenceType()).append("\n"); }
                if (result.decompileCompleted()) text.append(result.getDecompiledFunction().getC());
                else text.append("// DECOMPILE_FAILED: ").append(result.getErrorMessage()).append("\n");
                text.append("\n// Disassembly\n");
                InstructionIterator instructions = currentProgram.getListing().getInstructions(f.getBody(), true);
                while (instructions.hasNext()) {
                    Instruction instruction = instructions.next();
                    text.append("// ").append(instruction.getAddress()).append("  ").append(instruction).append("\n");
                }
                Files.writeString(root.resolve(f.getEntryPoint().toString()+".c"), text.toString());
                println("RADIO_DECOMPILED " + f.getName(true) + " @ " + f.getEntryPoint() + " success=" + result.decompileCompleted());
            }
        } finally { decompiler.dispose(); }
    }
}
