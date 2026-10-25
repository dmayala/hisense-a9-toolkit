package a9.aod;

import java.io.File;
import java.io.StringReader;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.transform.OutputKeys;
import javax.xml.transform.Transformer;
import javax.xml.transform.TransformerFactory;
import javax.xml.transform.dom.DOMSource;
import javax.xml.transform.stream.StreamResult;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.xml.sax.InputSource;

/** Offline SharedPreferences XML edit; caller stops the app and installs atomically. */
public final class Preferences {
    public static void main(String[] args) throws Exception {
        if (args.length != 2 || !(args[0].equals("get") || args[0].equals("overlay"))) {
            throw new IllegalArgumentException("get|overlay preferences.xml");
        }
        DocumentBuilder builder = DocumentBuilderFactory.newInstance().newDocumentBuilder();
        builder.setEntityResolver((publicId, systemId) -> new InputSource(new StringReader("")));
        File file = new File(args[1]);
        Document doc;
        if (file.exists()) {
            doc = builder.parse(file);
        } else {
            doc = builder.newDocument();
            doc.appendChild(doc.createElement("map"));
        }
        Element map = doc.getDocumentElement();
        if (map == null || !map.getTagName().equals("map")) {
            throw new IllegalArgumentException("not SharedPreferences XML");
        }
        String mode = "overlay"; // Same default as the inspected a9service 3.9.1 APK.
        for (Node node = map.getFirstChild(); node != null;) {
            Node next = node.getNextSibling();
            if (node instanceof Element && ((Element) node).getAttribute("name").equals("aod_mode")) {
                if (!((Element) node).getTagName().equals("string")) {
                    throw new IllegalArgumentException("aod_mode is not a string");
                }
                mode = node.getTextContent();
                if (args[0].equals("overlay")) map.removeChild(node);
            }
            node = next;
        }
        if (args[0].equals("get")) {
            // Print only the mode, never unrelated private preference values.
            System.out.println(mode);
            return;
        }
        Element value = doc.createElement("string");
        value.setAttribute("name", "aod_mode");
        value.setTextContent("overlay");
        map.appendChild(value);
        Transformer writer = TransformerFactory.newInstance().newTransformer();
        writer.setOutputProperty(OutputKeys.ENCODING, "UTF-8");
        writer.transform(new DOMSource(doc), new StreamResult(System.out));
    }
}
