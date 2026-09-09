#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コミュニケーション図の事前/事後条件・シナリオを .qeax とテキストの間で往復させる。

    python scripts/ea_scenarios.py export              # EA -> docs/analysis-analog/communication-scenarios.md
    python scripts/ea_scenarios.py import --dry-run    # 差分だけ表示
    python scripts/ea_scenarios.py import              # テキスト -> EA (自動バックアップあり)

事前/事後条件は t_objectconstraint、シナリオは t_objectscenarios に入っており、図に貼られた
ノートは t_object(Object_Type='Note') の PDATA3 に「制約文そのもの/シナリオ名」をキーとして
持っている。import はこのキーとノートの表示文字列 (Note) も一緒に書き換えるので、GUI で
制約文を直したときのようにノートの再選択が要らない。図の座標には触らない。

ノートがまだ貼られていない図には、ノート要素と NoteLink を既存図と同じレイアウトで作る
(--no-notes で抑止)。位置は後から EA で自由に動かしてよい。
"""
import argparse
import datetime
import glob
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid

PARENT_PKG = 15                     # コミュニケーション-シナリオ
ACTOR_NAME = 'User'                 # シナリオの器にしているアクター
AUTHOR = 'm-sasaki'
STATUS = '設計中'
CONSTRAINT_TYPES = ('事前条件', '事後条件')
DEFAULT_MD = os.path.join('docs', 'analysis-analog', 'communication-scenarios.md')

# ノート未配置のときに使う配置 (既存図 Diagram_ID=13 の実測値)。単位は EA の図座標で y は負。
NOTE_RECT = {
    '事前条件': (-155, 31, 227, -253),      # (Top, Left, Right, Bottom)
    'シナリオ': (-283, 31, 396, -473),
    '事後条件': (-509, 31, 210, -585),
}
NOTE_STACK_DX = 220                     # 同種が複数あるときは右へずらす (縦は帯を保つ)

HEADER = """<!-- このファイルと EA (話題沸騰ポット.qeax) は scripts/ea_scenarios.py で往復させる。
     export: EA -> このファイル / import: このファイル -> EA

     書式の決まり:
     - `## 見出し` は EA のパッケージ名と完全一致させる (コミュニケーション-シナリオ 直下)
     - `### 事前条件` / `### 事後条件` は、空行で区切ったブロック単位で 1 制約。
       ブロック内の複数行は「1 つの制約の中の複数行」として扱う (現状の EA の入り方と同じ)
     - `### シナリオ:基本` の `基本` がシナリオ名 (EA 側のキー)。`### シナリオ:別名 [代替]`
       のように後ろに [ ] を付けると ScenarioType を分けられる
     - 本文が空の節は「未記入」とみなして import では何もしない。EA 側の削除は手作業で
-->

# コミュニケーション図 事前/事後条件・シナリオ
"""


# ---------------------------------------------------------------- utilities

def find_db():
    hits = glob.glob('*.qeax')
    if not hits:
        sys.exit('*.qeax が見つからない。リポジトリのルートで実行すること。')
    return hits[0]


def ea_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq EA.exe'],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return False
    return 'EA.exe' in out


def ea_guid():
    u = str(uuid.uuid4()).upper().split('-')
    u[2] = u[2].lower()
    return '{' + '-'.join(u) + '}'


def duid():
    return '%08X' % random.getrandbits(32)


def crlf(s):
    return s.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')


def lf(s):
    return (s or '').replace('\r\n', '\n').replace('\r', '\n')


def scenes(con):
    """[(Package_ID, Name, Actor_Object_ID or None, Diagram_ID or None)] を Package_ID 順で返す。"""
    out = []
    for p in con.execute('select Package_ID,Name from t_package where Parent_ID=? order by Package_ID',
                         (PARENT_PKG,)):
        actor = con.execute("select Object_ID from t_object "
                            "where Package_ID=? and Object_Type='Actor' and Name=?",
                            (p['Package_ID'], ACTOR_NAME)).fetchone()
        diag = con.execute("select Diagram_ID from t_diagram "
                           "where Package_ID=? and Diagram_Type='Collaboration'",
                           (p['Package_ID'],)).fetchone()
        out.append((p['Package_ID'], p['Name'],
                    actor['Object_ID'] if actor else None,
                    diag['Diagram_ID'] if diag else None))
    return out


# ------------------------------------------------------------------ export

def do_export(con, path):
    parts = [HEADER]
    for pkg_id, name, actor_id, _ in scenes(con):
        parts.append('\n## %s\n' % name)
        if actor_id is None:
            parts.append('\n<!-- %s アクターが無い -->\n' % ACTOR_NAME)
            continue

        for ctype in CONSTRAINT_TYPES:
            rows = con.execute('select "Constraint" c from t_objectconstraint '
                               'where Object_ID=? and ConstraintType=? order by rowid',
                               (actor_id, ctype)).fetchall()
            parts.append('\n### %s\n\n' % ctype)
            if rows:
                parts.append('\n\n'.join(lf(r['c']).strip() for r in rows) + '\n')

        found = False
        for r in con.execute('select Scenario,ScenarioType,Notes from t_objectscenarios '
                             'where Object_ID=? order by rowid', (actor_id,)):
            found = True
            head = '### シナリオ:%s' % r['Scenario']
            if r['ScenarioType'] and r['ScenarioType'] != r['Scenario']:
                head += ' [%s]' % r['ScenarioType']
            body = lf(r['Notes']).strip()
            parts.append('\n%s\n\n' % head + (body + '\n' if body else ''))
        if not found:
            parts.append('\n### シナリオ:基本\n\n')

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(''.join(parts))
    print('wrote:', path)


# ------------------------------------------------------------------- parse

RE_SCENE = re.compile(r'^##\s+(?P<name>\S.*?)\s*$')
RE_SECTION = re.compile(r'^###\s*(?P<kind>事前条件|事後条件|シナリオ)'
                        r'(?:[:：]\s*(?P<name>[^\[\]]+?))?\s*'
                        r'(?:\[(?P<type>[^\]]+)\])?\s*$')


def parse_md(path, known_names=None):
    """-> [(scene_name, [(kind, name, type, body_text)])]

    known_names を渡すと、それに一致する `## 見出し` だけをシーンの区切りとみなす。
    シナリオ本文にコメントとして `##` で始まる行が来ても壊れないようにするため。
    """
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)

    out, cur, sec = [], None, None

    def close_sec():
        if sec is not None:
            kind, nm, ty, buf = sec
            cur[1].append((kind, nm, ty, '\n'.join(buf).strip()))

    for line in text.splitlines():
        m = RE_SCENE.match(line)
        if m and (known_names is None or m.group('name') in known_names):
            close_sec()
            sec = None
            cur = (m.group('name'), [])
            out.append(cur)
            continue
        m = RE_SECTION.match(line)
        if m and cur is not None:
            close_sec()
            sec = (m.group('kind'), (m.group('name') or '').strip(),
                   (m.group('type') or '').strip(), [])
            continue
        if sec is not None:
            sec[3].append(line)
    close_sec()
    return out


# ------------------------------------------------------------------ import

class Importer:
    def __init__(self, con, place_notes=True, dry=False):
        self.con = con
        self.place_notes = place_notes
        self.dry = dry
        self.now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log = []

    def say(self, fmt, *a):
        self.log.append(fmt % a)
        print(('  [dry] ' if self.dry else '  ') + fmt % a)

    # -- notes ------------------------------------------------------------
    def note_row(self, actor_id, kind, key):
        return self.con.execute(
            "select Object_ID from t_object where Object_Type='Note' "
            "and PDATA1=? and PDATA2=? and PDATA3=?",
            (kind, str(actor_id), key)).fetchone()

    def note_text(self, kind, ctype, key, body):
        if kind == 'Constraint':
            return crlf('«%s»\n{%s}' % (ctype, body))
        return crlf('%s\n%s' % (key, body))

    def update_note(self, actor_id, kind, old_key, new_key, ctype, body):
        row = self.note_row(actor_id, kind, old_key)
        if not row:
            return False
        if not self.dry:
            self.con.execute('update t_object set PDATA3=?,Note=?,ModifiedDate=? where Object_ID=?',
                             (crlf(new_key) if kind == 'Constraint' else new_key,
                              self.note_text(kind, ctype, new_key, body), self.now, row['Object_ID']))
        return True

    def create_note(self, pkg_id, diag_id, actor_id, kind, ctype, key, body, index):
        if not self.place_notes or diag_id is None:
            return
        guid = ea_guid()
        pdata3 = crlf(key) if kind == 'Constraint' else key
        if self.dry:
            self.say('note (new) %s / %s', ctype, key.splitlines()[0][:30])
            return
        cur = self.con.cursor()
        cur.execute("""insert into t_object
            (Object_Type,Diagram_ID,Author,Version,Note,Package_ID,NType,Complexity,Effort,
             Backcolor,BorderStyle,BorderWidth,Fontcolor,Bordercolor,CreatedDate,ModifiedDate,
             Status,Abstract,Tagged,PDATA1,PDATA2,PDATA3,PDATA4,GenType,Phase,Scope,Classifier,
             ea_guid,ParentID,IsRoot,IsLeaf,IsSpec,IsActive)
            values ('Note',0,?,'1.0',?,?,0,'1',0,-1,0,-1,-1,-1,?,?,?,'0',0,?,?,?,'Yes','<none>',
                    '1.0','Public',0,?,0,0,0,0,0)""",
            (AUTHOR, self.note_text(kind, ctype, key, body), pkg_id, self.now, self.now, STATUS,
             kind, str(actor_id), pdata3, guid))
        note_id = cur.lastrowid

        top, left, right, bottom = NOTE_RECT['シナリオ' if kind == 'Scenario' else ctype]
        left += NOTE_STACK_DX * index
        right += NOTE_STACK_DX * index
        note_duid = duid()
        seq = (self.con.execute('select ifnull(max(Sequence),0)+1 s from t_diagramobjects '
                                'where Diagram_ID=?', (diag_id,)).fetchone()['s'])
        cur.execute("""insert into t_diagramobjects
            (Diagram_ID,Object_ID,RectTop,RectLeft,RectRight,RectBottom,Sequence,ObjectStyle)
            values (?,?,?,?,?,?,?,?)""",
            (diag_id, note_id, top, left, right, bottom, seq, 'DUID=%s;' % note_duid))

        cur.execute("""insert into t_connector
            (Direction,Connector_Type,SourceAccess,DestAccess,SourceContainment,DestContainment,
             Start_Object_ID,End_Object_ID,RouteStyle,LineColor,VirtualInheritance,PDATA5,ea_guid,
             DestIsNavigable,SourceChangeable,DestChangeable,SourceTS,DestTS,SourceStyle,DestStyle)
            values ('Source -> Destination','NoteLink','Public','Public','Unspecified','Unspecified',
                    ?,?,3,-1,'0','SX=0;SY=0;EX=0;EY=0;',?,1,'none','none','instance','instance',
                    'Union=0;Derived=0;AllowDuplicates=0;','Union=0;Derived=0;AllowDuplicates=0;')""",
            (note_id, actor_id, ea_guid()))
        conn_id = cur.lastrowid

        arow = self.con.execute('select ObjectStyle from t_diagramobjects '
                                'where Diagram_ID=? and Object_ID=?', (diag_id, actor_id)).fetchone()
        m = re.search(r'DUID=([0-9A-F]+);', arow['ObjectStyle'] or '') if arow else None
        actor_duid = m.group(1) if m else duid()
        cur.execute("""insert into t_diagramlinks (DiagramID,ConnectorID,Geometry,Style,Hidden)
            values (?,?,'SX=0;SY=0;EX=0;EY=0;EDGE=1;$LLB=;LLT=;LMT=;LMB=;LRT=;LRB=;IRHS=;ILHS=;',?,0)""",
            (diag_id, conn_id, 'Mode=3;EOID=%s;SOID=%s;Color=-1;LWidth=0;' % (actor_duid, note_duid)))
        self.say('note (new) %s / %s', ctype, key.splitlines()[0][:30])

    # -- constraints ------------------------------------------------------
    def sync_constraints(self, pkg_id, diag_id, actor_id, ctype, blocks):
        if not blocks:
            return
        old = self.con.execute('select rowid rid,"Constraint" c from t_objectconstraint '
                               'where Object_ID=? and ConstraintType=? order by rowid',
                               (actor_id, ctype)).fetchall()
        for i, body in enumerate(blocks):
            new = crlf(body)
            if i < len(old):
                if lf(old[i]['c']).strip() == body:
                    continue
                if not self.dry:
                    self.con.execute('update t_objectconstraint set "Constraint"=? where rowid=?',
                                     (new, old[i]['rid']))
                relinked = self.update_note(actor_id, 'Constraint', old[i]['c'], new, ctype, body)
                self.say('%s 更新%s: %s', ctype, '' if relinked else ' (ノート未配置)',
                         body.splitlines()[0][:40])
            else:
                if not self.dry:
                    self.con.execute('insert into t_objectconstraint '
                                     '(Object_ID,"Constraint",ConstraintType,Weight,Status) '
                                     "values (?,?,?,0.0,'承認済')", (actor_id, new, ctype))
                self.say('%s 追加: %s', ctype, body.splitlines()[0][:40])
                self.create_note(pkg_id, diag_id, actor_id, 'Constraint', ctype, new, body, i)
        if len(old) > len(blocks):
            self.say('!! %s が EA 側に %d 件余っている (削除はしない)', ctype, len(old) - len(blocks))

    # -- scenarios --------------------------------------------------------
    def sync_scenario(self, pkg_id, diag_id, actor_id, name, stype, body, index):
        if not body:
            return
        stype = stype or name
        row = self.con.execute('select rowid rid,Notes,ScenarioType from t_objectscenarios '
                               'where Object_ID=? and Scenario=?', (actor_id, name)).fetchone()
        new = crlf(body)
        if row:
            if lf(row['Notes']).strip() == body and (row['ScenarioType'] or '') == stype:
                return
            if not self.dry:
                self.con.execute('update t_objectscenarios set Notes=?,ScenarioType=? where rowid=?',
                                 (new, stype, row['rid']))
            relinked = self.update_note(actor_id, 'Scenario', name, name, stype, body)
            self.say('シナリオ 更新%s: %s', '' if relinked else ' (ノート未配置)', name)
        else:
            if not self.dry:
                self.con.execute("""insert into t_objectscenarios
                    (Object_ID,Scenario,ScenarioType,EValue,Notes,XMLContent,ea_guid)
                    values (?,?,?,0.0,?,'<path>\n\t<context/>\n</path>',?)""",
                    (actor_id, name, stype, new, ea_guid()))
            self.say('シナリオ 追加: %s', name)
            self.create_note(pkg_id, diag_id, actor_id, 'Scenario', stype, name, body, index)

    # -- driver -----------------------------------------------------------
    def run(self, doc):
        known = {name: (pid, aid, did) for pid, name, aid, did in scenes(self.con)}
        for scene_name, sections in doc:
            if scene_name not in known:
                print('!! EA にパッケージが無い:', scene_name)
                continue
            pkg_id, actor_id, diag_id = known[scene_name]
            if actor_id is None:
                print('!! %s アクターが無い: %s' % (ACTOR_NAME, scene_name))
                continue
            print('#', scene_name)
            n_sc = 0
            for kind, name, stype, body in sections:
                if kind in CONSTRAINT_TYPES:
                    blocks = [b.strip() for b in re.split(r'\n\s*\n', body) if b.strip()]
                    self.sync_constraints(pkg_id, diag_id, actor_id, kind, blocks)
                else:
                    self.sync_scenario(pkg_id, diag_id, actor_id, name or '基本', stype, body, n_sc)
                    n_sc += 1


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=['export', 'import'])
    ap.add_argument('-f', '--file', default=DEFAULT_MD)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-notes', action='store_true', help='ノート未配置でも図に貼らない')
    ap.add_argument('--force', action='store_true', help='EA が起動中でも実行する')
    args = ap.parse_args()

    if ea_running() and not args.force and not (args.mode == 'export' or args.dry_run):
        sys.exit('EA が起動中。閉じてから実行すること (--force で無視)。')

    db = find_db()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    if args.mode == 'export':
        do_export(con, args.file)
        con.close()
        return

    doc = parse_md(args.file, {name for _, name, _, _ in scenes(con)})
    if not args.dry_run:
        bak = db + datetime.datetime.now().strftime('.bak_%Y%m%d_%H%M%S')
        shutil.copy2(db, bak)
        print('backup:', bak)
    Importer(con, place_notes=not args.no_notes, dry=args.dry_run).run(doc)
    if args.dry_run:
        con.rollback()
        print('dry-run: 変更していない。')
    else:
        con.commit()
        print('done.')
    con.close()


if __name__ == '__main__':
    main()
