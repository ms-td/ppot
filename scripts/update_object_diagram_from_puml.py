#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/analysis-analog/object-*.puml の内容を、既存の EA オブジェクト図へ差分で反映する。

scripts/create_object_diagram_from_puml.py がパッケージごと新規に作るのに対し、
こちらは **既にある** パッケージ/図を更新する。EA 側で手で整えた配置を壊さないため。

    python scripts/update_object_diagram_from_puml.py --dry-run
    python scripts/update_object_diagram_from_puml.py

やること:
  - 属性:   puml のオブジェクト本文1行 = t_attribute 1行 (既存の作りに合わせる) で入れ替え
  - 要素:   puml にあって EA に無いオブジェクトを追加し、図の空き位置に置く
  - 改名:   同じ位置づけで名前だけ変わったものを rename (RENAMES で指定)
  - 関連:   puml の線と EA のコネクタを突き合わせ、不足を追加・余分を削除
  - ノート: 転記しない (このプロジェクトの既存方針。図の注釈はユーザーの編集に任せる)
"""
import argparse
import datetime
import glob
import io
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
import uuid

AUTHOR = 'm-sasaki'

# puml ファイル -> EA のパッケージ名
TARGETS = [
    ('docs/analysis-analog/object-01-水なし蓋閉.puml', '水無し 蓋閉じ 通電'),
    ('docs/analysis-analog/object-02-給水線沸騰開始.puml', '給水線沸騰開始'),
    ('docs/analysis-analog/object-04-沸騰後温度下がらずエラー.puml', '沸騰後温度下がらずエラー'),
]

# EA 側の旧名 -> puml の新名
RENAMES = {'第1,2,3,4:第n水位センサ': '第1,2,3,4:水位センサ'}

NEW_W, NEW_H = 200, 70          # 追加要素の既定サイズ
NEW_GAP = 40


def ea_guid():
    u = str(uuid.uuid4()).upper().split('-')
    u[2] = u[2].lower()
    return '{' + '-'.join(u) + '}'


def duid():
    return '%08X' % random.getrandbits(32)


def ea_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq EA.exe'],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return False
    return 'EA.exe' in out


# ---------------------------------------------------------------- puml 解析

RE_OBJ = re.compile(r'^object\s+"([^"]*)"\s+as\s+(\w+)\s*(?:#\S+\s*)?(\{)?\s*$')
RE_LINK = re.compile(r'^(\w+)\s*(--|<\.\.|\.\.>|\.\.)\s*(\w+)\s*(?::.*)?$')


def parse_puml(path):
    """-> (alias->表示名, [attr行...] の dict, [(alias_a, alias_b, 種別)])"""
    names, attrs, links = {}, {}, []
    cur = None
    for raw in io.open(path, encoding='utf-8'):
        line = raw.rstrip('\n').strip()
        if cur is not None:                      # オブジェクト本文の中
            if line == '}':
                cur = None
            elif line:
                attrs[cur].append(line)
            continue
        m = RE_OBJ.match(line)
        if m:
            names[m.group(2)] = m.group(1)
            attrs[m.group(2)] = []
            if m.group(3):
                cur = m.group(2)
            continue
        if line.startswith("'") or '[hidden]' in line:
            continue
        m = RE_LINK.match(line)
        if m and m.group(1) in names and m.group(3) in names:
            kind = 'Association' if m.group(2) == '--' else 'Dependency'
            a, b = m.group(1), m.group(3)
            if m.group(2) == '<..':              # `ht <.. hp` は hp -> ht
                a, b = b, a
            # puml は 4 本の実体を表すのに同じ線を 4 回引いているが、
            # EA 側は 1 本で持っているので重複は畳む
            if (a, b, kind) not in links and (b, a, kind) not in links:
                links.append((a, b, kind))
    return names, attrs, links


# ------------------------------------------------------------------- EA 側

class Updater:
    def __init__(self, con, dry):
        self.con, self.dry = con, dry
        self.now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.n = {'attr': 0, 'obj': 0, 'rename': 0, 'conn+': 0, 'conn-': 0}

    def say(self, fmt, *a):
        print(('  [dry] ' if self.dry else '  ') + (fmt % a))

    # -- 要素 ---------------------------------------------------------
    def add_object(self, pkg_id, diag_id, name, body, pos):
        cur = self.con.cursor()
        cur.execute("""insert into t_object
            (Object_Type,Diagram_ID,Name,Author,Version,Package_ID,NType,Complexity,Effort,
             Backcolor,BorderStyle,BorderWidth,Fontcolor,Bordercolor,CreatedDate,ModifiedDate,
             Abstract,Tagged,GenType,Phase,Scope,Classifier,ea_guid,ParentID,
             IsRoot,IsLeaf,IsSpec,IsActive)
            values ('Class',0,?,?,'1.0',?,0,'2',0,-1,0,-1,-1,-1,?,?,'0',0,'<none>','1.0',
                    'Public',0,?,0,0,0,0,0)""",
            (name, AUTHOR, pkg_id, self.now, self.now, ea_guid()))
        oid = cur.lastrowid
        self.set_attrs(oid, body)
        left, top = pos
        cur.execute("""insert into t_diagramobjects
            (Diagram_ID,Object_ID,RectTop,RectLeft,RectRight,RectBottom,Sequence,ObjectStyle)
            values (?,?,?,?,?,?,1,?)""",
            (diag_id, oid, top, left, left + NEW_W, top - NEW_H, 'DUID=%s;' % duid()))
        self.say('要素 追加: %s (属性%d行)', name, len(body))
        self.n['obj'] += 1
        return oid

    def set_attrs(self, oid, body):
        cur = self.con.cursor()
        cur.execute('delete from t_attribute where Object_ID=?', (oid,))
        for i, line in enumerate(body):
            cur.execute("""insert into t_attribute
                (Object_ID,Name,Scope,Pos,ea_guid,Const,IsStatic,IsCollection,IsOrdered,
                 AllowDuplicates,Derived,Length,Precision,Scale)
                values (?,?,'Public',?,?,0,0,0,0,0,0,0,0,0)""",
                (oid, line, i, ea_guid()))

    # -- コネクタ -----------------------------------------------------
    def add_conn(self, diag_id, a, b, kind, duids):
        cur = self.con.cursor()
        cur.execute("""insert into t_connector
            (Direction,Connector_Type,SourceAccess,DestAccess,SourceContainment,DestContainment,
             Start_Object_ID,End_Object_ID,RouteStyle,LineColor,VirtualInheritance,PDATA5,ea_guid,
             SourceIsNavigable,DestIsNavigable,SourceChangeable,DestChangeable,SourceTS,DestTS,
             SourceStyle,DestStyle)
            values ('Unspecified',?,'Public','Public','Unspecified','Unspecified',
                    ?,?,3,-1,'0','SX=0;SY=0;EX=0;EY=0;',?,0,0,'none','none','instance','instance',
                    'Union=0;Derived=0;AllowDuplicates=0;','Union=0;Derived=0;AllowDuplicates=0;')""",
            (kind, a, b, ea_guid()))
        cid = cur.lastrowid
        cur.execute("""insert into t_diagramlinks (DiagramID,ConnectorID,Geometry,Style,Hidden)
            values (?,?,'SX=0;SY=0;EX=0;EY=0;EDGE=1;$LLB=;LLT=;LMT=;LMB=;LRT=;LRB=;IRHS=;ILHS=;',?,0)""",
            (diag_id, cid,
             'Mode=3;EOID=%s;SOID=%s;Color=-1;LWidth=0;' % (duids.get(b, duid()), duids.get(a, duid()))))
        na = self.con.execute('select Name from t_object where Object_ID=?', (a,)).fetchone()
        nb = self.con.execute('select Name from t_object where Object_ID=?', (b,)).fetchone()
        self.say('関連 追加: %s -- %s', na['Name'], nb['Name'])
        self.n['conn+'] += 1

    def del_conn(self, cid):
        self.con.execute('delete from t_diagramlinks where ConnectorID=?', (cid,))
        self.con.execute('delete from t_connector where Connector_ID=?', (cid,))
        self.n['conn-'] += 1

    # -- 1 パッケージ分 -----------------------------------------------
    def run_one(self, puml, pkg_name):
        con = self.con
        pkg = con.execute('select Package_ID,Name from t_package where Name=?', (pkg_name,)).fetchone()
        if not pkg:
            print('!! パッケージが無い:', pkg_name); return
        pid = pkg['Package_ID']
        diag = con.execute("select Diagram_ID from t_diagram where Package_ID=?", (pid,)).fetchone()
        did = diag['Diagram_ID']
        print('#', pkg_name)

        names, attrs, links = parse_puml(puml)

        # 改名
        for old, new in RENAMES.items():
            r = con.execute("select Object_ID from t_object where Package_ID=? and Name=?", (pid, old)).fetchone()
            if r:
                con.execute('update t_object set Name=?,ModifiedDate=? where Object_ID=?',
                            (new, self.now, r['Object_ID']))
                self.say('改名: %s -> %s', old, new); self.n['rename'] += 1

        byname = {r['Name']: r['Object_ID'] for r in
                  con.execute("select Object_ID,Name from t_object where Package_ID=? and Object_Type='Class'", (pid,))}

        # 追加する要素の置き場所 (既存の右下の空き)
        ext = con.execute("""select min(RectLeft) l, min(RectBottom) b from t_diagramobjects
                             where Diagram_ID=?""", (did,)).fetchone()
        nx, ny = (ext['l'] or 30), (ext['b'] or -100) - 80

        alias2id = {}
        for al, nm in names.items():
            body = attrs[al]
            if nm in byname:
                oid = byname[nm]
                old = [a['Name'] for a in con.execute(
                    'select Name from t_attribute where Object_ID=? order by Pos', (oid,))]
                if old != body:
                    self.set_attrs(oid, body)
                    self.say('属性 更新: %s (%d行 -> %d行)', nm, len(old), len(body))
                    self.n['attr'] += 1
            else:
                oid = self.add_object(pid, did, nm, body, (nx, ny))
                nx += NEW_W + NEW_GAP
            alias2id[al] = oid

        # 図上の DUID (コネクタの Style に要る)
        duids = {}
        for r in con.execute('select Object_ID,ObjectStyle from t_diagramobjects where Diagram_ID=?', (did,)):
            m = re.search(r'DUID=([0-9A-F]+);', r['ObjectStyle'] or '')
            if m: duids[r['Object_ID']] = m.group(1)

        # コネクタの突き合わせ (向きは無視して 1 本ずつ対応させる)
        want = []
        for a, b, kind in links:
            ia, ib = alias2id.get(a), alias2id.get(b)
            if ia and ib: want.append((frozenset((ia, ib)), kind))
        have = []
        for r in con.execute("""select c.Connector_ID,c.Connector_Type,c.Start_Object_ID,c.End_Object_ID
               from t_connector c join t_diagramlinks l on l.ConnectorID=c.Connector_ID
               where l.DiagramID=?""", (did,)):
            have.append((frozenset((r['Start_Object_ID'], r['End_Object_ID'])), r['Connector_Type'], r['Connector_ID']))

        rest = list(have)
        for key, kind in want:
            hit = next((h for h in rest if h[0] == key and h[1] == kind), None)
            if hit: rest.remove(hit)
            else:
                a, b = (list(key) + list(key))[:2]
                for x, y, k in links:
                    if alias2id.get(x) in key and alias2id.get(y) in key and k == kind:
                        a, b = alias2id[x], alias2id[y]; break
                self.add_conn(did, a, b, kind, duids)
        for key, kind, cid in rest:
            nmm = [con.execute('select Name from t_object where Object_ID=?', (o,)).fetchone() for o in key]
            self.say('関連 削除: %s', ' -- '.join(str(x['Name']) if x else '?' for x in nmm))
            self.del_conn(cid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()
    if ea_running() and not args.force and not args.dry_run:
        sys.exit('EA が起動中。閉じてから実行すること (--force で無視)。')
    db = next(h for h in glob.glob('*.qeax')
              if unicodedata.normalize('NFC', h) == '話題沸騰ポット.qeax')
    if not args.dry_run:
        bak = db + datetime.datetime.now().strftime('.bak_%Y%m%d_%H%M%S')
        shutil.copy2(db, bak); print('backup:', bak)
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    up = Updater(con, args.dry_run)
    for puml, pkg in TARGETS:
        up.run_one(puml, pkg)
    if args.dry_run:
        con.rollback(); print('dry-run: 変更していない。')
    else:
        con.commit(); print('done.')
    print('内訳:', up.n)
    con.close()


if __name__ == '__main__':
    main()
