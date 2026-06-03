using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;

using DbLine = Autodesk.AutoCAD.DatabaseServices.Line;

namespace DrawingCompareWorkbench.AutodeskDwgJsonExtractor
{
    public static class Program
    {
        public static int Main(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine("usage: extractor.exe <input.dwg> <acadver> <output.json> <max-entities>");
                return 2;
            }

            string input = Path.GetFullPath(args[0]);
            string acadver = args[1].ToUpperInvariant();
            string output = Path.GetFullPath(args[2]);
            int maxEntities = Math.Max(1, int.Parse(args[3], CultureInfo.InvariantCulture));

            HostApplicationServices host = new BridgeHost();
            try
            {
                RuntimeSystem.Initialize(host, 0);
                using (Database database = new Database(false, true))
                {
                    database.ReadDwgFile(input, FileOpenMode.OpenForReadAndAllShare, true, "");
                    database.CloseInput(true);
                    string json = ExtractDrawingJson(database, input, acadver, maxEntities);
                    Directory.CreateDirectory(Path.GetDirectoryName(output));
                    File.WriteAllText(output, json, new UTF8Encoding(false));
                }
                return 0;
            }
            catch (System.Exception exc)
            {
                Console.Error.WriteLine(exc.GetType().FullName + ": " + exc.Message);
                return 1;
            }
            finally
            {
                try
                {
                    RuntimeSystem.Terminate();
                }
                catch
                {
                    // Terminate can throw when initialization failed before the runtime was ready.
                }
            }
        }

        private static string ExtractDrawingJson(Database database, string input, string requestedAcadver, int maxEntities)
        {
            StringBuilder json = new StringBuilder(1024 * 1024);
            int entityCount = 0;
            int unsupportedCount = 0;
            bool truncated = false;
            Dictionary<string, int> unsupportedByType = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                json.Append('{');
                json.Append("\"header\":{");
                JsonPair(json, "$ACADVER", database.OriginalFileVersion.ToString());
                json.Append(',');
                JsonPair(json, "$REQUESTED_ACADVER", requestedAcadver);
                json.Append(',');
                JsonPair(json, "$INSUNITS", Convert.ToInt32(database.Insunits, CultureInfo.InvariantCulture));
                json.Append("},");

                WriteLayers(json, database, transaction);
                json.Append(',');
                json.Append("\"entities\":[");

                BlockTable blockTable = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
                BlockTableRecord modelSpace =
                    (BlockTableRecord)transaction.GetObject(blockTable[BlockTableRecord.ModelSpace], OpenMode.ForRead);
                bool first = true;
                foreach (ObjectId id in modelSpace)
                {
                    if (entityCount >= maxEntities)
                    {
                        truncated = true;
                        break;
                    }

                    Entity entity = transaction.GetObject(id, OpenMode.ForRead, false) as Entity;
                    if (entity == null)
                    {
                        continue;
                    }

                    string entityJson = EntityJson(entity, transaction);
                    if (entityJson == null)
                    {
                        string rawType = entity.GetType().Name.ToUpperInvariant();
                        unsupportedCount++;
                        unsupportedByType[rawType] = unsupportedByType.ContainsKey(rawType) ? unsupportedByType[rawType] + 1 : 1;
                        entityJson = UnsupportedEntityJson(entity, rawType);
                    }

                    if (!first)
                    {
                        json.Append(',');
                    }
                    json.Append(entityJson);
                    first = false;
                    entityCount++;
                }

                json.Append("],");
                json.Append("\"metadata\":{");
                JsonPair(json, "source_path", input);
                json.Append(',');
                json.Append("\"autodesk_dwg_json_bridge\":{");
                JsonPair(json, "runtime", "autodesk-managed-standalone");
                json.Append(',');
                JsonPair(json, "database_original_file_version", database.OriginalFileVersion.ToString());
                json.Append(',');
                JsonPair(json, "requested_acadver", requestedAcadver);
                json.Append(',');
                JsonPair(json, "entity_count", entityCount);
                json.Append(',');
                JsonPair(json, "unsupported_entity_count", unsupportedCount);
                json.Append(',');
                JsonPair(json, "truncated", truncated);
                json.Append(',');
                WriteUnsupportedTypes(json, unsupportedByType);
                json.Append("}}");
                json.Append('}');
                transaction.Commit();
            }

            return json.ToString();
        }

        private static void WriteLayers(StringBuilder json, Database database, Transaction transaction)
        {
            json.Append("\"layers\":[");
            LayerTable layerTable = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
            bool first = true;
            foreach (ObjectId id in layerTable)
            {
                LayerTableRecord layer = (LayerTableRecord)transaction.GetObject(id, OpenMode.ForRead);
                if (!first)
                {
                    json.Append(',');
                }
                json.Append('{');
                JsonPair(json, "name", layer.Name);
                json.Append(',');
                JsonPair(json, "locked", layer.IsLocked);
                json.Append(',');
                JsonPair(json, "frozen", layer.IsFrozen);
                json.Append("}");
                first = false;
            }
            json.Append(']');
        }

        private static string EntityJson(Entity entity, Transaction transaction)
        {
            DbLine line = entity as DbLine;
            if (line != null)
            {
                return CommonPrefix("LINE", entity) +
                    "\"start\":" + PointJson(line.StartPoint) +
                    ",\"end\":" + PointJson(line.EndPoint) +
                    "}}";
            }

            Circle circle = entity as Circle;
            if (circle != null)
            {
                return CommonPrefix("CIRCLE", entity) +
                    "\"center\":" + PointJson(circle.Center) +
                    ",\"radius\":" + Num(circle.Radius) +
                    "}}";
            }

            Arc arc = entity as Arc;
            if (arc != null)
            {
                return CommonPrefix("ARC", entity) +
                    "\"center\":" + PointJson(arc.Center) +
                    ",\"radius\":" + Num(arc.Radius) +
                    ",\"start_angle_deg\":" + Num(Degrees(arc.StartAngle)) +
                    ",\"end_angle_deg\":" + Num(Degrees(arc.EndAngle)) +
                    ",\"normal\":" + VectorJson(arc.Normal) +
                    "}}";
            }

            Polyline polyline = entity as Polyline;
            if (polyline != null)
            {
                return CommonPrefix("LWPOLYLINE", entity) +
                    "\"vertices\":" + PolylineVerticesJson(polyline) +
                    ",\"closed\":" + Bool(polyline.Closed) +
                    "}}";
            }

            Polyline2d polyline2d = entity as Polyline2d;
            if (polyline2d != null)
            {
                return CommonPrefix("POLYLINE", entity) +
                    "\"vertices\":" + Polyline2dVerticesJson(polyline2d, transaction) +
                    ",\"closed\":" + Bool(polyline2d.Closed) +
                    "}}";
            }

            Polyline3d polyline3d = entity as Polyline3d;
            if (polyline3d != null)
            {
                return CommonPrefix("POLYLINE", entity) +
                    "\"vertices\":" + Polyline3dVerticesJson(polyline3d, transaction) +
                    ",\"closed\":" + Bool(polyline3d.Closed) +
                    "}}";
            }

            DBText text = entity as DBText;
            if (text != null)
            {
                return CommonPrefix("TEXT", entity) +
                    "\"insert\":" + PointJson(text.Position) +
                    ",\"height\":" + Num(text.Height) +
                    ",\"text\":" + JsonString(text.TextString) +
                    ",\"rotation_deg\":" + Num(Degrees(text.Rotation)) +
                    ",\"alignment\":" + JsonString(Convert.ToInt32(text.HorizontalMode, CultureInfo.InvariantCulture) + ":" + Convert.ToInt32(text.VerticalMode, CultureInfo.InvariantCulture)) +
                    "}}";
            }

            MText mtext = entity as MText;
            if (mtext != null)
            {
                return CommonPrefix("MTEXT", entity) +
                    "\"insert\":" + PointJson(mtext.Location) +
                    ",\"height\":" + Num(mtext.TextHeight) +
                    ",\"raw_content\":" + JsonString(mtext.Contents) +
                    ",\"plain_text\":" + JsonString(mtext.Contents) +
                    ",\"rotation_deg\":" + Num(Degrees(mtext.Rotation)) +
                    "}}";
            }

            BlockReference block = entity as BlockReference;
            if (block != null)
            {
                return CommonPrefix("INSERT", entity) +
                    "\"insert\":" + PointJson(block.Position) +
                    ",\"scale\":" + ScaleJson(block.ScaleFactors) +
                    ",\"rotation_deg\":" + Num(Degrees(block.Rotation)) +
                    ",\"block_name\":" + JsonString(BlockName(block, transaction)) +
                    ",\"attributes\":" + AttributesJson(block, transaction) +
                    "}}";
            }

            return null;
        }

        private static string CommonPrefix(string type, Entity entity)
        {
            return "{" +
                "\"type\":" + JsonString(type) +
                ",\"layer\":" + JsonString(entity.Layer ?? "0") +
                ",\"handle\":" + JsonString(entity.Handle.ToString()) +
                ",\"geometry\":{";
        }

        private static string UnsupportedEntityJson(Entity entity, string rawType)
        {
            return CommonPrefix(rawType, entity) + "}}";
        }

        private static string PolylineVerticesJson(Polyline polyline)
        {
            StringBuilder json = new StringBuilder();
            json.Append('[');
            for (int index = 0; index < polyline.NumberOfVertices; index++)
            {
                if (index > 0)
                {
                    json.Append(',');
                }
                json.Append("{\"point\":");
                json.Append(PointJson(polyline.GetPoint3dAt(index)));
                json.Append(",\"bulge\":");
                json.Append(Num(polyline.GetBulgeAt(index)));
                json.Append('}');
            }
            json.Append(']');
            return json.ToString();
        }

        private static string Polyline2dVerticesJson(Polyline2d polyline, Transaction transaction)
        {
            StringBuilder json = new StringBuilder();
            json.Append('[');
            bool first = true;
            foreach (ObjectId id in polyline)
            {
                Vertex2d vertex = transaction.GetObject(id, OpenMode.ForRead, false) as Vertex2d;
                if (vertex == null)
                {
                    continue;
                }
                if (!first)
                {
                    json.Append(',');
                }
                json.Append("{\"point\":");
                json.Append(PointJson(vertex.Position));
                json.Append(",\"bulge\":");
                json.Append(Num(vertex.Bulge));
                json.Append('}');
                first = false;
            }
            json.Append(']');
            return json.ToString();
        }

        private static string Polyline3dVerticesJson(Polyline3d polyline, Transaction transaction)
        {
            StringBuilder json = new StringBuilder();
            json.Append('[');
            bool first = true;
            foreach (ObjectId id in polyline)
            {
                PolylineVertex3d vertex = transaction.GetObject(id, OpenMode.ForRead, false) as PolylineVertex3d;
                if (vertex == null)
                {
                    continue;
                }
                if (!first)
                {
                    json.Append(',');
                }
                json.Append("{\"point\":");
                json.Append(PointJson(vertex.Position));
                json.Append(",\"bulge\":0.0}");
                first = false;
            }
            json.Append(']');
            return json.ToString();
        }

        private static string AttributesJson(BlockReference block, Transaction transaction)
        {
            StringBuilder json = new StringBuilder();
            json.Append('[');
            bool first = true;
            foreach (ObjectId id in block.AttributeCollection)
            {
                AttributeReference attribute = transaction.GetObject(id, OpenMode.ForRead, false) as AttributeReference;
                if (attribute == null)
                {
                    continue;
                }
                if (!first)
                {
                    json.Append(',');
                }
                json.Append('{');
                JsonPair(json, "tag", attribute.Tag);
                json.Append(',');
                JsonPair(json, "text", attribute.TextString);
                json.Append(',');
                json.Append("\"insert\":");
                json.Append(PointJson(attribute.Position));
                json.Append(',');
                JsonPair(json, "height", attribute.Height);
                json.Append('}');
                first = false;
            }
            json.Append(']');
            return json.ToString();
        }

        private static string BlockName(BlockReference block, Transaction transaction)
        {
            BlockTableRecord record = transaction.GetObject(block.BlockTableRecord, OpenMode.ForRead, false) as BlockTableRecord;
            return record == null ? "" : record.Name;
        }

        private static void WriteUnsupportedTypes(StringBuilder json, Dictionary<string, int> unsupportedByType)
        {
            json.Append("\"unsupported_entity_types\":{");
            bool first = true;
            foreach (KeyValuePair<string, int> item in unsupportedByType)
            {
                if (!first)
                {
                    json.Append(',');
                }
                JsonPair(json, item.Key, item.Value);
                first = false;
            }
            json.Append('}');
        }

        private static string PointJson(Point3d point)
        {
            return "[" + Num(point.X) + "," + Num(point.Y) + "," + Num(point.Z) + "]";
        }

        private static string VectorJson(Vector3d vector)
        {
            return "[" + Num(vector.X) + "," + Num(vector.Y) + "," + Num(vector.Z) + "]";
        }

        private static string ScaleJson(Scale3d scale)
        {
            return "[" + Num(scale.X) + "," + Num(scale.Y) + "," + Num(scale.Z) + "]";
        }

        private static double Degrees(double radians)
        {
            return radians * 180.0 / Math.PI;
        }

        private static string Bool(bool value)
        {
            return value ? "true" : "false";
        }

        private static string Num(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value))
            {
                return "0.0";
            }
            return value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static void JsonPair(StringBuilder json, string key, string value)
        {
            json.Append(JsonString(key));
            json.Append(':');
            json.Append(JsonString(value ?? ""));
        }

        private static void JsonPair(StringBuilder json, string key, int value)
        {
            json.Append(JsonString(key));
            json.Append(':');
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void JsonPair(StringBuilder json, string key, double value)
        {
            json.Append(JsonString(key));
            json.Append(':');
            json.Append(Num(value));
        }

        private static void JsonPair(StringBuilder json, string key, bool value)
        {
            json.Append(JsonString(key));
            json.Append(':');
            json.Append(Bool(value));
        }

        private static string JsonString(string value)
        {
            if (value == null)
            {
                return "\"\"";
            }

            StringBuilder json = new StringBuilder(value.Length + 2);
            json.Append('"');
            foreach (char ch in value)
            {
                switch (ch)
                {
                    case '\\':
                        json.Append("\\\\");
                        break;
                    case '"':
                        json.Append("\\\"");
                        break;
                    case '\b':
                        json.Append("\\b");
                        break;
                    case '\f':
                        json.Append("\\f");
                        break;
                    case '\n':
                        json.Append("\\n");
                        break;
                    case '\r':
                        json.Append("\\r");
                        break;
                    case '\t':
                        json.Append("\\t");
                        break;
                    default:
                        if (ch < ' ')
                        {
                            json.Append("\\u");
                            json.Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            json.Append(ch);
                        }
                        break;
                }
            }
            json.Append('"');
            return json.ToString();
        }
    }

    internal sealed class BridgeHost : HostApplicationServices
    {
        public override string FindFile(string fileName, Database database, FindFileHint hint)
        {
            if (File.Exists(fileName))
            {
                return Path.GetFullPath(fileName);
            }
            return fileName;
        }
    }
}
