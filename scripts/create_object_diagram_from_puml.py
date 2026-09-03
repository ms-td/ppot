#!/usr/bin/env python3
"""
Create a new EA package + Object diagram + all its elements/attributes/
connectors in 話題沸騰ポット.qeax, from one of the
docs/analysis-analog/object-*.puml reference diagrams.

Unlike scripts/add_object_diagram_connectors.py (which only adds connectors
to an already-populated diagram), this script builds the whole thing from
scratch: package, package-as-object row, diagram, one Class-typed element per
puml `object`, its attribute compartment (one t_attribute row per body line,
including PlantUML separator lines like "===" / "----" verbatim, matching
the convention already used in the "PlantUML" package), a simple grid
placement on the diagram, and the association/dependency connectors.

Notes on conventions this mirrors (reverse-engineered from the existing
"PlantUML/水無し 蓋閉じ 通電" and "PlantUML/沸騰後温度下がらずエラー" packages):
  - A package is represented twice: a t_package row, AND a t_object row
    (Object_Type='Package') living in the *parent* package, sharing the same
    ea_guid. The t_object row's PDATA1 holds the child t_package.Package_ID
    as a string.
  - Each puml `object` becomes a t_object row with Object_Type='Class' (not
    'Object' — this project deliberately uses class-shaped elements on
    Object diagrams; see the explanatory Note object already in each of
    those packages).
  - Each line of a puml object's body becomes one t_attribute row, Name=the
    literal line text, Pos=its 0-based order. This includes PlantUML's
    "===" / "---" / "----" field separators verbatim.
  - Diagram placement links an element to its diagram-local DUID via
    t_diagramobjects.ObjectStyle ("DUID=XXXXXXXX;..."), and connectors
    reference that DUID via t_diagramlinks.Style ("EOID=..;SOID=..").
  - PlantUML notes (`note as X ... end note`) in the reference diagrams are
    NOT transcribed into EA — by established convention for this project,
    only objects/attributes/relations are mechanically reflected; diagram
    commentary is left to the user's own editing.

Usage (from repo root):
    python scripts/create_object_diagram_from_puml.py <puml_path> <parent_package_name> <new_package_name> <new_diagram_name>

Example:
    python scripts/create_object_diagram_from_puml.py \
        "docs/analysis-analog/object-02-給水線沸騰開始.puml" \
        "PlantUML" "給水線沸騰開始" "給水線沸騰開始"
"""
import glob
import re
import sqlite3
import sys
import uuid

DB_PATH = glob.glob("話題沸騰ポット.qeax")[0]

# Static EA diagram-default strings, copied from an existing sibling diagram
# in the same "PlantUML" package (Diagram_ID=6, "沸騰後温度下がらずエラー") —
# these are just EA's default view/theme settings, not content-specific.
DIAGRAM_PDATA = (
    "HideRel=0;ShowTags=0;ShowReqs=0;ShowCons=0;OpParams=1;ShowSN=0;ScalePI=0;"
    "PPgs.cx=0;PPgs.cy=0;PSize=9;ShowIcons=1;SuppCN=0;HideProps=0;HideParents=0;"
    "UseAlias=0;HideAtts=0;HideOps=0;HideStereo=0;HideEStereo=0;ShowRec=1;"
    "ShowRes=0;ShowShape=1;FormName=;"
)
DIAGRAM_SWIMLANES = (
    "locked=false;orientation=0;width=0;inbar=false;names=false;color=-1;"
    "bold=false;fcol=0;tcol=-1;ofCol=-1;ufCol=-1;hl=1;ufh=0;hh=0;cls=0;bw=0;"
    "hli=0;bro=0;"
)
DIAGRAM_STYLEEX = (
    "ExcludeRTF=0;DocAll=0;HideQuals=0;AttPkg=1;ShowTests=0;ShowMaint=0;"
    "SuppressFOC=1;MatrixActive=0;SwimlanesActive=1;KanbanActive=0;"
    "MatrixLineWidth=1;MatrixLineClr=0;MatrixLocked=0;TConnectorNotation=UML 2.1;"
    "TExplicitNavigability=0;AdvancedElementProps=1;AdvancedFeatureProps=1;"
    "AdvancedConnectorProps=1;m_bElementClassifier=1;SPT=1;MDGDgm=;"
    "MDGView=UML::Simple Object;STBLDgm=;ShowNotes=0;VisibleAttributeDetail=0;"
    "ShowOpRetType=1;SuppressBrackets=0;SuppConnectorLabels=0;"
    "PrintPageHeadFoot=0;ShowAsList=0;SuppressedCompartments=;Theme=:119;"
    "SaveTag=00000000;"
)

AUTHOR = "m-sasaki"


def new_guid():
    return "{" + str(uuid.uuid4()).upper() + "}"


def new_duid():
    return uuid.uuid4().hex[:8].upper()


def parse_puml(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    objects = []  # (alias, name, color, body_lines)
    associations = []
    seen_assoc = set()
    dependencies = []
    i = 0
    obj_re = re.compile(r'^object "(.*)" as (\w+)(?: #(\w+))?\s*\{$')
    assoc_re = re.compile(r'^(\w+)\s*--\s*(\w+)$')
    dep_re = re.compile(r'^(\w+)\s*<\.\.\s*(\w+)\s*:\s*(.+)$')
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        m = obj_re.match(s)
        if m:
            name, alias, color = m.groups()
            body = []
            i += 1
            while lines[i].strip() != "}":
                body.append(lines[i].strip())
                i += 1
            objects.append((alias, name, color, body))
            i += 1
            continue
        if s and not s.startswith("'") and "[hidden]" not in s:
            m = assoc_re.match(s)
            if m:
                a, b = m.groups()
                key = tuple(sorted((a, b)))
                if key not in seen_assoc:
                    seen_assoc.add(key)
                    associations.append((a, b))
                i += 1
                continue
            m = dep_re.match(s)
            if m:
                dst, src, label = m.groups()
                dependencies.append((src, dst, label))
                i += 1
                continue
        i += 1
    return objects, associations, dependencies


def main():
    if len(sys.argv) != 5:
        raise SystemExit(
            f"usage: {sys.argv[0]} <puml_path> <parent_package_name> "
            "<new_package_name> <new_diagram_name>"
        )
    puml_path, parent_name, new_pkg_name, new_diagram_name = sys.argv[1:5]

    objects, associations, dependencies = parse_puml(puml_path)
    if len(objects) == 0:
        raise SystemExit("no `object \"...\" as alias { ... }` blocks found in puml")
    print(f"parsed {len(objects)} objects, {len(associations)} associations, "
          f"{len(dependencies)} dependencies from {puml_path}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT Package_ID FROM t_package WHERE Name=?", (parent_name,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"parent package {parent_name!r} not found in t_package")
    parent_package_id = row[0]

    # 1) new t_package
    cur.execute(
        "INSERT INTO t_package (Name, Parent_ID, Version) VALUES (?, ?, '1.0')",
        (new_pkg_name, parent_package_id),
    )
    new_package_id = cur.lastrowid
    pkg_guid = new_guid()
    cur.execute(
        "UPDATE t_package SET ea_guid=? WHERE Package_ID=?",
        (pkg_guid, new_package_id),
    )

    # 2) t_object row representing the package itself, inside the parent
    cur.execute(
        """
        INSERT INTO t_object (
            Object_Type, Name, Author, Version, Package_ID,
            Complexity, Backcolor, BorderWidth, Fontcolor, Bordercolor,
            Status, Abstract, PDATA1, GenType, Phase, Scope, Classifier,
            ea_guid, ParentID
        ) VALUES (
            'Package', ?, ?, '1.0', ?,
            '1', -1, -1, -1, -1,
            '設計中', '0', ?, 'Java', '1.0', 'Public', 0,
            ?, 0
        )
        """,
        (new_pkg_name, AUTHOR, parent_package_id, str(new_package_id), pkg_guid),
    )

    # 3) t_diagram
    cur.execute(
        """
        INSERT INTO t_diagram (
            Package_ID, Diagram_Type, Name, Version, Author,
            AttPub, AttPri, AttPro, Orientation, Scale,
            ShowForeign, ShowBorder, ShowPackageContents,
            PDATA, ea_guid, Swimlanes, StyleEx
        ) VALUES (
            ?, 'Object', ?, '1.0', ?,
            1, 1, 1, 'P', 100,
            1, 1, 1,
            ?, ?, ?, ?
        )
        """,
        (
            new_package_id, new_diagram_name, AUTHOR,
            DIAGRAM_PDATA, new_guid(), DIAGRAM_SWIMLANES, DIAGRAM_STYLEEX,
        ),
    )
    diagram_id = cur.lastrowid

    # 4) elements + attributes + diagram placement
    oid = {}
    duid = {}
    X_STEP, Y_STEP, COLS = 320, 320, 5
    for idx, (alias, name, color, body) in enumerate(objects):
        cur.execute(
            """
            INSERT INTO t_object (
                Object_Type, Name, Package_ID,
                Backcolor, BorderWidth, Fontcolor, Bordercolor,
                Abstract, PDATA4, ea_guid
            ) VALUES ('Class', ?, ?, -1, -1, -1, -1, '0', '0', ?)
            """,
            (name, new_package_id, new_guid()),
        )
        object_id = cur.lastrowid
        oid[alias] = object_id

        for pos, body_line in enumerate(body):
            cur.execute(
                """
                INSERT INTO t_attribute (Object_ID, Name, Pos, ea_guid)
                VALUES (?, ?, ?, ?)
                """,
                (object_id, body_line, pos, new_guid()),
            )

        max_len = max([len(name)] + [len(b) for b in body]) if body else len(name)
        width = max(160, 16 * max_len + 40)
        height = 40 + 18 * len(body)
        col, rowi = idx % COLS, idx // COLS
        left = 40 + col * X_STEP
        top = -(40 + rowi * Y_STEP)
        object_duid = new_duid()
        duid[alias] = object_duid
        cur.execute(
            """
            INSERT INTO t_diagramobjects (
                Diagram_ID, Object_ID, RectTop, RectLeft, RectRight, RectBottom,
                Sequence, ObjectStyle
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                diagram_id, object_id, top, left, left + width, top - height,
                idx + 1,
                f"DUID={object_duid};NSL=0;BCol=-1;BFol=-1;LCol=-1;LWth=-1;"
                f"fontsz=0;bold=0;black=0;italic=0;ul=0;charset=0;pitch=0;",
            ),
        )

    # 5) connectors + diagram links
    def insert_connector(start_alias, end_alias, connector_type, direction,
                          name=None, dest_navigable=False):
        start_id, end_id = oid[start_alias], oid[end_alias]
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
                SourceAccess, DestAccess, SourceContainment, DestContainment,
                Start_Object_ID, End_Object_ID,
                RouteStyle, LineColor, VirtualInheritance, PDATA5, DiagramID,
                ea_guid, DestIsNavigable, SourceChangeable, DestChangeable,
                SourceTS, DestTS, Target2, SourceStyle, DestStyle
            ) VALUES (
                ?, ?, ?,
                'Public', 'Public', 'Unspecified', 'Unspecified',
                ?, ?,
                3, -1, '0', 'SX=0;SY=0;EX=0;EY=0;', 0,
                ?, ?, 'none', 'none',
                'instance', 'instance', 0, ?, ?
            )
            """,
            (
                name, direction, connector_type,
                start_id, end_id,
                new_guid(), 1 if dest_navigable else 0,
                source_style, dest_style,
            ),
        )
        connector_id = cur.lastrowid
        style = f"Mode=3;EOID={duid[end_alias]};SOID={duid[start_alias]};Color=-1;LWidth=0;"
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
    for a, b in associations:
        created.append(insert_connector(a, b, "Association", "Unspecified"))
    for src, dst, label in dependencies:
        created.append(
            insert_connector(src, dst, "Dependency", "Source -> Destination",
                              name=label, dest_navigable=True)
        )

    conn.commit()
    print(f"created Package_ID={new_package_id}, Diagram_ID={diagram_id}, "
          f"{len(oid)} elements, {len(created)} connectors")
    conn.close()


if __name__ == "__main__":
    main()
