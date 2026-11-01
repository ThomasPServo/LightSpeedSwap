// Inventory actual Mach-O functions/strings and xrefs relevant to cellular reads.
// @category LightSpeedRadio
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import com.google.gson.GsonBuilder;
import java.nio.file.*;
import java.util.*;

public class RadioInventory extends GhidraScript {
    private boolean wanted(String name) {
        String s = name.toLowerCase(Locale.ROOT);
        return s.contains("internetconnectionstate") || s.contains("internetdatastatus") ||
            s.contains("copycellinfo") || s.contains("getcellinfo") || s.contains("refreshcellmonitor") ||
            s.contains("qosflow") || s.contains("cellularqos") || s.contains("entitlement") ||
            s.contains("fine-grained") || s.equals("qci") || s.contains("cellmonitorbandinfo") ||
            s.contains("getconnectionstate") || s.contains("getdatastatus") ||
            s.contains("accesscontrollist") || s.contains("getcurrentdataservice") || s.contains("getactivecontexts");
    }
    public void run() throws Exception {
        Map<String,Object> report = new LinkedHashMap<>();
        report.put("program", currentProgram.getName());
        report.put("executable_sha256", currentProgram.getExecutableSHA256());
        report.put("language", currentProgram.getLanguageID().toString());
        report.put("image_base", currentProgram.getImageBase().toString());
        List<Map<String,Object>> functions = new ArrayList<>();
        FunctionIterator fi = currentProgram.getFunctionManager().getFunctions(true);
        while (fi.hasNext() && !monitor.isCancelled()) {
            Function f = fi.next();
            if (wanted(f.getName(true))) {
                Map<String,Object> item = new LinkedHashMap<>();
                item.put("name", f.getName(true)); item.put("address", f.getEntryPoint().toString());
                item.put("size", f.getBody().getNumAddresses());
                functions.add(item);
            }
        }
        List<Map<String,Object>> strings = new ArrayList<>();
        DataIterator it = currentProgram.getListing().getDefinedData(true);
        while (it.hasNext() && !monitor.isCancelled()) {
            Data data = it.next();
            if (!data.hasStringValue()) continue;
            String value = String.valueOf(data.getValue());
            if (!wanted(value)) continue;
            Map<String,Object> item = new LinkedHashMap<>();
            item.put("text", value); item.put("address", data.getAddress().toString());
            List<Map<String,String>> refs = new ArrayList<>();
            Set<Address> seen = new HashSet<>();
            List<Address> layer = new ArrayList<>(); layer.add(data.getAddress());
            for (int depth = 0; depth < 3 && !layer.isEmpty(); depth++) {
                List<Address> next = new ArrayList<>();
                for (Address address : layer) {
                    ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(address);
                    while (ri.hasNext() && refs.size() < 100) {
                        Reference r = ri.next(); Address from = r.getFromAddress();
                        if (!seen.add(from)) continue;
                        Function f = getFunctionContaining(from);
                        Map<String,String> ref = new LinkedHashMap<>();
                        ref.put("from", from.toString()); ref.put("type", r.getReferenceType().toString());
                        ref.put("depth", Integer.toString(depth));
                        if (f != null) { ref.put("function", f.getName(true)); ref.put("entry", f.getEntryPoint().toString()); }
                        else next.add(from);
                        refs.add(ref);
                    }
                }
                layer = next;
            }
            item.put("references", refs); strings.add(item);
        }
        report.put("functions", functions); report.put("strings", strings);
        Path out = Paths.get(getScriptArgs()[0]); Files.createDirectories(out.getParent());
        Files.writeString(out, new GsonBuilder().setPrettyPrinting().create().toJson(report));
        println("RADIO_INVENTORY " + currentProgram.getName() + " functions=" + functions.size() + " strings=" + strings.size() + " output=" + out);
    }
}
