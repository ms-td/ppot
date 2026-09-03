#!/usr/bin/env python3
"""
Add connectors (relations) to an EA Object diagram in 話題沸騰ポット.qeax,
mirroring the associations/dependency drawn in one of the
docs/analysis-analog/object-*.puml reference diagrams (they all share the
same 25-object / 31-relation shape, only attribute values differ).

Usage (from repo root):
    python scripts/add_object_diagram_connectors.py <DIAGRAM_ID> <BASE_OBJECT_ID>

<BASE_OBJECT_ID> is the Object_ID of the "水位" (sl) object in the target
package; the rest are addressed by the same fixed offsets used across every
one of these object diagrams (they're always pasted as one contiguous block
of 25 objects in puml order).
"""
import glob
import sqlite3
import sys
import uuid

DB_PATH = glob.glob("話題沸騰ポット.qeax")[0]

# Offsets from the "sl" (水位) Object_ID, matching the object order in the
# object-*.puml files (and confirmed against package 8's 51..75 range).
OFFSETS = {
    "sl": 0, "s1": 1, "s5": 2, "c1": 3,
    "vm": 4, "s6": 5,
    "op": 6, "b2": 7, "pp": 8, "ll": 9,
    "tm": 10, "ht": 11, "hp": 12, "ts": 13, "ss": 14,
    "wm": 15, "kl": 16, "bl": 17, "b1": 18, "b3": 19,
    "mm": 20, "ms": 21, "b5": 22,
    "error": 23,
    "bz": 24,
}

# Associations (undirected, as in the puml's "--" lines; duplicates collapsed)
ASSOCIATIONS = [
    ("sl", "s1"), ("sl", "s5"), ("sl", "c1"),
    ("vm", "s6"),
    ("ll", "op"), ("op", "pp"), ("op", "b2"),
    ("tm", "ht"), ("tm", "hp"), ("tm", "ss"), ("tm", "ts"),
    ("mm", "b5"), ("mm", "ms"), ("mm", "vm"),
    ("wm", "b1"), ("wm", "b3"), ("wm", "kl"), ("wm", "bl"), ("wm", "bz"),
    ("sl", "wm"), ("wm", "op"), ("wm", "tm"),
    ("vm", "wm"), ("vm", "op"), ("wm", "mm"),
    ("tm", "error"), ("wm", "error"),
    ("bz", "error"), ("bz", "mm"), ("bz", "op"),
]

# Dependency: "ht <.. hp : 電力の供給" => hp ..> ht (hp depends on / drives ht)
DEPENDENCIES = [
    ("hp", "ht", "電力の供給"),
]


def new_guid():
    return "{" + str(uuid.uuid4()).upper() + "}"


def main():
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <DIAGRAM_ID> <BASE_OBJECT_ID>")
    diagram_id = int(sys.argv[1])
    base_id = int(sys.argv[2])
    oid = {alias: base_id + off for alias, off in OFFSETS.items()}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # DUID per Object_ID for this diagram (used to link connector endpoints
    # to their on-diagram box in t_diagramlinks.Style)
    cur.execute(
        "SELECT Object_ID, ObjectStyle FROM t_diagramobjects WHERE Diagram_ID=?",
        (diagram_id,),
    )
    duid = {}
    for object_id, style in cur.fetchall():
        for part in style.split(";"):
            if part.startswith("DUID="):
                duid[object_id] = part.split("=", 1)[1]
                break

    missing = [name for name in oid if oid[name] not in duid]
    if missing:
        raise SystemExit(f"missing DUID for objects: {missing}")

    def insert_connector(start_name, end_name, connector_type, direction,
                          name=None, dest_navigable=False):
        start_id = oid[start_name]
        end_id = oid[end_name]
        source_style = "Union=0;Derived=0;AllowDuplicates=0;Owned=0;Navigable=%s;" % (
            "Non-Navigable" if dest_navigable else "Unspecified"
        )
        dest_style = "Union=0;Derived=0;AllowDuplicates=0;Owned=0;Navigable=%s;" % (
            "Navigable" if dest_navigable else "Unspecified"
        )
        cur.execute(
            """
            INSERT INTO t_connector (
                Name, Direction, Connector_Type,
                SourceAccess, DestAccess,
                SourceContainment, DestContainment,
                SourceIsAggregate, SourceIsOrdered, DestIsAggregate, DestIsOrdered,
                Start_Object_ID, End_Object_ID,
                Start_Edge, End_Edge, PtStartX, PtStartY, PtEndX, PtEndY,
                SeqNo, HeadStyle, LineStyle, RouteStyle, IsBold, LineColor,
                VirtualInheritance, PDATA5, DiagramID, ea_guid,
                SourceIsNavigable, DestIsNavigable, IsRoot, IsLeaf,
                SourceChangeable, DestChangeable, SourceTS, DestTS, Target2,
                SourceStyle, DestStyle
            ) VALUES (
                ?, ?, ?,
                'Public', 'Public',
                'Unspecified', 'Unspecified',
                0, 0, 0, 0,
                ?, ?,
                0, 0, 0, 0, 0, 0,
                0, 0, 0, 3, 0, -1,
                '0', 'SX=0;SY=0;EX=0;EY=0;', 0, ?,
                0, ?, 0, 0,
                'none', 'none', 'instance', 'instance', 0,
                ?, ?
            )
            """,
            (
                name, direction, connector_type,
                start_id, end_id,
                new_guid(),
                1 if dest_navigable else 0,
                source_style, dest_style,
            ),
        )
        connector_id = cur.lastrowid

        style = f"Mode=3;EOID={duid[end_id]};SOID={duid[start_id]};Color=-1;LWidth=0;"
        geometry = "SX=0;SY=0;EX=0;EY=0;EDGE=0;$LLB=;LLT=;LMT=;LMB=;LRT=;LRB=;IRHS=;ILHS=;"
        cur.execute(
            """
            INSERT INTO t_diagramlinks (DiagramID, ConnectorID, Geometry, Style, Hidden, Path)
            VALUES (?, ?, ?, ?, 0, NULL)
            """,
            (diagram_id, connector_id, geometry, style),
        )
        return connector_id

    created = []
    for a, b in ASSOCIATIONS:
        created.append(insert_connector(a, b, "Association", "Unspecified"))

    for src, dst, label in DEPENDENCIES:
        created.append(
            insert_connector(
                src, dst, "Dependency", "Source -> Destination",
                name=label, dest_navigable=True,
            )
        )

    conn.commit()
    print(f"inserted {len(created)} connectors: {created}")
    conn.close()


if __name__ == "__main__":
    main()
