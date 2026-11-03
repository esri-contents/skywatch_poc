using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace GwangmyeongInterpretation
{
    /// <summary>
    /// 검수표 qa/&lt;회차&gt;/qa_labels.csv(UTF-8 BOM) 읽기·쓰기. 열: sample_id, stratum, population, pnu, x, y, label, note (+ judge).
    /// 파이썬 qa.summarize·stratified_precision은 label·note만 읽으므로 judge 열을 더해도 된다. 다른 열 순서와 값은 그대로 둔다.
    /// </summary>
    internal class QaTable
    {
        public static readonly (string code, string text)[] Labels = { ("structure", "구조물 맞음"), ("not_structure", "구조물 아님"), ("uncertain", "판단 불가") };

        public string Path { get; }
        public List<string> Header { get; }
        public List<string[]> Rows { get; }

        private QaTable(string path, List<string> header, List<string[]> rows) { Path = path; Header = header; Rows = rows; }

        public int Col(string name) => Header.IndexOf(name);

        public string Get(int row, string col) { var i = Col(col); return i >= 0 && i < Rows[row].Length ? Rows[row][i] : ""; }

        public void Set(int row, string col, string value)
        {
            var i = Col(col);
            if (i < 0) { Header.Add(col); i = Header.Count - 1; }
            if (Rows[row].Length <= i)
            {
                var r = Rows[row];
                Array.Resize(ref r, Header.Count);
                Rows[row] = r;
            }
            Rows[row][i] = value ?? "";
        }

        public bool IsLabeled(int row) => Labels.Any(l => l.code == Get(row, "label"));

        public int Done => Enumerable.Range(0, Rows.Count).Count(IsLabeled);

        public static QaTable Load(string path)
        {
            var lines = Parse(File.ReadAllText(path, Encoding.UTF8));
            var header = lines.First().ToList();
            var rows = lines.Skip(1).Where(l => l.Length > 1 || (l.Length == 1 && l[0] != "")).Select(l =>
            {
                var a = l;
                if (a.Length < header.Count) Array.Resize(ref a, header.Count);
                return a.Select(v => v ?? "").ToArray();
            }).ToList();
            return new QaTable(path, header, rows);
        }

        public void Save()
        {
            var sb = new StringBuilder();
            sb.Append(string.Join(",", Header.Select(Quote))).Append("\r\n");
            foreach (var r in Rows)
                sb.Append(string.Join(",", Enumerable.Range(0, Header.Count).Select(i => Quote(i < r.Length ? r[i] : "")))).Append("\r\n");
            var tmp = Path + ".tmp";
            File.WriteAllText(tmp, sb.ToString(), new UTF8Encoding(true));
            File.Move(tmp, Path, overwrite: true);
        }

        private static string Quote(string v) =>
            v.IndexOfAny(new[] { ',', '"', '\r', '\n' }) >= 0 ? "\"" + v.Replace("\"", "\"\"") + "\"" : v;

        /// <summary>RFC 4180 CSV(따옴표 안 쉼표·줄바꿈·"" 이스케이프).</summary>
        public static List<string[]> Parse(string text)
        {
            var rows = new List<string[]>();
            var row = new List<string>();
            var cell = new StringBuilder();
            bool q = false;
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (q)
                {
                    if (c == '"' && i + 1 < text.Length && text[i + 1] == '"') { cell.Append('"'); i++; }
                    else if (c == '"') q = false;
                    else cell.Append(c);
                }
                else if (c == '"') q = true;
                else if (c == ',') { row.Add(cell.ToString()); cell.Clear(); }
                else if (c == '\n' || c == '\r')
                {
                    if (c == '\r' && i + 1 < text.Length && text[i + 1] == '\n') i++;
                    row.Add(cell.ToString()); cell.Clear();
                    rows.Add(row.ToArray()); row.Clear();
                }
                else if (c != '﻿') cell.Append(c);
            }
            if (cell.Length > 0 || row.Count > 0) { row.Add(cell.ToString()); rows.Add(row.ToArray()); }
            return rows;
        }
    }
}
