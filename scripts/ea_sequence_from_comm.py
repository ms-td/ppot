"""コミュニケーション図からシーケンス図を起こす。

    python scripts/ea_sequence_from_comm.py --dry-run          # 何を作るかだけ表示
    python scripts/ea_sequence_from_comm.py                    # 分析 に作る
    python scripts/ea_sequence_from_comm.py -m 分析-クラス指定

<モデル>/コミュニケーション-シナリオ/<場面> の Collaboration 図 (場面の先頭の図。変種は使わない) を元に、
<モデル>/シーケンス図/<短い名前> パッケージへ、同名の Sequence 図を作る。手作業で作り始めた
分析/シーケンス図/蓋閉 の作りに合わせてある:

- ライフラインはシーケンス図のパッケージに新しく作るインスタンス (名前と分類子はコミュニケーション図から)。
  同じパッケージに同じ名前・分類子のインスタンスが既にあれば、それを使う
- 場面の User アクター (シナリオの器) をそのまま置き、「基本」シナリオのノートを新しく作ってノートリンクで
  つなぐ (ノートのキーは同じなので ea_scenarios.py の import で一緒に更新される)
- シナリオの番号付き手順 "N. Userは〜を…" ごとに、User からその相手へのメッセージ (名前なし) を、
  相手が最初にメッセージを送る直前に入れる
- メッセージはコミュニケーション図の順 (SeqNo) で、名前・引数・戻り値・操作の対応 (operation_guid) を写す。
  戻り値のある同期呼び出しには、呼び出しの入れ子が閉じるところで、値を名前にした戻りメッセージを足し、
  呼び出し側の戻り値は空にする (UML では戻り値は応答メッセージに載せる。コミュニケーション図側は残す)
  (PDATA4 = '1'。通常の呼び出しは '0' にする。空だと EA が戻りとして描くことがある)
- 既に図がある場合: 既存のメッセージは消さない。向きと名前が同じものは順に使い回し (空の引数・戻り値・
  操作の対応はコミュニケーション図から埋める)、足りない分だけ足す。ライフラインとノートの位置は振り直す

座標は機械的に並べるだけなので、見た目の調整は EA で行う。
"""
import argparse
import datetime
import glob
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
import uuid

SCENES = {   # コミュニケーション図の場面パッケージ名 -> シーケンス図の名前
    '蓋閉から沸騰開始を経由して保温まで': '蓋閉',
    '沸騰ボタン押下から沸騰開始まで': '沸騰ボタン',
    '保温モードボタン押下から保温モードが変更されるまで': '保温モードボタン',
    '解除ボタン押下から給湯モードが解除されるを経由して給湯ボタンで給湯されるまで': '解除と給湯',
    '解除ボタン押下から給湯モードがロックされるまで': '解除とロック',
    '貯水部の水がなくなり保温が停止されるまで': '水なし',
    '沸騰開始から高温エラー発生まで': '高温エラー',
    '沸騰開始から温度上がらずエラー発生まで': '温度上がらずエラー',
    '保温開始から温度下がらずエラー発生まで': '温度下がらずエラー',
}
COMM_PKG, SEQ_PKG, ACTOR, SCENARIO = 'コミュニケーション-シナリオ', 'シーケンス図', 'User', '基本'

# 配置 (EA の図座標。y は下向きに負)
NOTE_L, NOTE_R, TOP = 25, 278, -50
LIFE_W, LIFE_PITCH, FIRST_X = 130, 170, 300
MSG_Y0, MSG_DY = -170, 40

OBJ_STYLE = 'DUID={};'
ACTOR_STYLE = ('DUID={};NSL=0;BCol=-1;BFol=-1;LCol=-1;LWth=-1;fontsz=0;bold=0;black=0;italic=0;'
               'ul=0;charset=0;pitch=0;')
NOTELINK_STYLE = 'Mode=3;EOID={};SOID={};Color=-1;LWidth=0;'
MSG_PDATA5 = 'SX=0;SY=0;EX=0;EY=0;$LLB=;LLT=;LMT=;LMB=;LRT=;LRB=;IRHS=;ILHS=;'


def guid():
    return '{%s}' % str(uuid.uuid4()).upper()


def duid():
    return uuid.uuid4().hex[:8].upper()


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def row(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def insert(con, table, r, pk=None):
    r = {k: v for k, v in r.items() if k != pk}
    return con.execute(f"insert into {table} ({','.join('[%s]' % k for k in r)}) values "
                       f"({','.join('?' * len(r))})", list(r.values())).lastrowid


def find_db():
    hits = [h for h in glob.glob('*.qeax') if unicodedata.normalize('NFC', h) == '話題沸騰ポット.qeax']
    if not hits:
        sys.exit('話題沸騰ポット.qeax が見つからない。リポジトリのルートで実行すること。')
    return hits[0]


def ea_running():
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              'Get-Process EA -ErrorAction SilentlyContinue | Select-Object -First 1 Id'],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return False
    return bool(re.search(r'\d', out))


def child_pkg(con, parent, name):
    r = con.execute("select Package_ID from t_package where Parent_ID=? and Name=?", (parent, name)).fetchall()
    return r[0][0] if len(r) == 1 else None


class Gen:
    def __init__(self, con, model):
        self.con = con
        top = con.execute("select Package_ID from t_package where Name=?", (model,)).fetchall()
        if len(top) != 1:
            sys.exit(f'{model} が {len(top)} 個ある。')
        self.top = top[0][0]
        self.comm = child_pkg(con, self.top, COMM_PKG)
        self.seq = child_pkg(con, self.top, SEQ_PKG) or self.new_package(self.top, SEQ_PKG)
        self.tmpl = self.templates()

    # 手作業で作った 分析/シーケンス図/蓋閉 の行を雛形にする (EA が書いた既定値をそのまま使うため)
    def templates(self):
        c = self.con
        d = row(c, "select * from t_diagram where Diagram_Type='Sequence' order by Diagram_ID limit 1")
        if not d:
            sys.exit('雛形にする Sequence 図が見つからない。EA で 1 枚作ってから実行すること。')
        o = row(c, "select o.* from t_diagramobjects x join t_object o on o.Object_ID=x.Object_ID "
                   "where x.Diagram_ID=? and o.Object_Type='Object' limit 1", (d['Diagram_ID'],))
        m = row(c, "select * from t_connector where Connector_Type='Sequence' and DiagramID=? limit 1", (d['Diagram_ID'],))
        p = row(c, "select * from t_package where Package_ID=?", (d['Package_ID'],))
        if not (o and m):
            sys.exit('雛形の Sequence 図にインスタンスとメッセージが要る。')
        return {'diagram': d, 'object': o, 'message': m, 'package': p,
                'xref': row(c, "select * from t_xref where Client=? and Name='CustomProperties'", (d['ea_guid'],))}

    def new_package(self, parent, name):
        c, ts = self.con, now()
        tp = row(c, "select * from t_package where Package_ID=?", (parent,))
        g = guid()
        pid = insert(c, 't_package', dict(tp, Name=name, Parent_ID=parent, ea_guid=g, CreatedDate=ts, ModifiedDate=ts,
                                          Notes=None, TPos=None), 'Package_ID')
        po = row(c, "select * from t_object where Object_Type='Package' and PDATA1=?", (str(parent),))
        insert(c, 't_object', dict(po, Name=name, Package_ID=parent, PDATA1=str(pid), ea_guid=g, CreatedDate=ts,
                                   ModifiedDate=ts, Note=None, TPos=None), 'Object_ID')
        return pid

    def op_guid(self, msg_name, lifeline):
        """受け手の分類子 (と汎化の親) から、メッセージ名と同名の操作の GUID を引く。"""
        name = re.sub(r'\(.*$', '', (msg_name or '').split('::')[-1]).strip()
        cl = self.con.execute("select Classifier from t_object where Object_ID=?", (lifeline,)).fetchone()[0]
        seen = [cl] if cl else []
        for x in seen:
            g = self.con.execute("select ea_guid from t_operation where Object_ID=? and Name=?", (x, name)).fetchone()
            if g:
                return g[0]
            seen += [r[0] for r in self.con.execute("select End_Object_ID from t_connector where Connector_Type='Generalization' "
                                                    "and Start_Object_ID=?", (x,)) if r[0] not in seen]
        return None

    def scene(self, comm_name, seq_name, log):
        c = self.con
        cpk = child_pkg(c, self.comm, comm_name)
        cdiag = c.execute("select Diagram_ID from t_diagram where Package_ID=? and Diagram_Type='Collaboration' "
                          "order by Diagram_ID limit 1", (cpk,)).fetchone()[0]
        actor = c.execute("select Object_ID from t_object where Package_ID=? and Object_Type='Actor' and Name=?",
                          (cpk, ACTOR)).fetchone()[0]
        scen = row(c, "select * from t_objectscenarios where Object_ID=? and Scenario=?", (actor, SCENARIO))
        msgs = [dict(r) for r in c.execute("select * from t_connector where DiagramID=? and Connector_Type='Collaboration' "
                                           "order by SeqNo", (cdiag,))]
        ts = now()

        # パッケージと図
        spk = child_pkg(c, self.seq, seq_name) or self.new_package(self.seq, seq_name)
        d = row(c, "select * from t_diagram where Package_ID=? and Diagram_Type='Sequence' and Name=?", (spk, seq_name))
        if d:
            did = d['Diagram_ID']
        else:
            t = self.tmpl['diagram']
            g = guid()
            did = insert(c, 't_diagram', dict(t, Name=seq_name, Package_ID=spk, ParentID=0, ea_guid=g,
                                              CreatedDate=ts, ModifiedDate=ts, Notes=None), 'Diagram_ID')
            if self.tmpl['xref']:
                insert(c, 't_xref', dict(self.tmpl['xref'], XrefID=guid(), Client=g))
        log.append(f'{seq_name}: diagram {"existing" if d else "new"} {did}')

        # ライフライン (コミュニケーション図で最初に出てくる順)
        order = []
        for m in msgs:
            for o in (m['Start_Object_ID'], m['End_Object_ID']):
                if o not in order:
                    order.append(o)
        life = {}
        for o in order:
            src = row(c, "select Name, Classifier, Classifier_guid from t_object where Object_ID=?", (o,))
            hit = c.execute("select Object_ID from t_object where Package_ID=? and Object_Type='Object' "
                            "and ifnull(Name,'')=ifnull(?,'') and Classifier=?", (spk, src['Name'], src['Classifier'])).fetchone()
            if hit:
                life[o] = hit[0]
            else:
                life[o] = insert(c, 't_object', dict(self.tmpl['object'], Name=src['Name'], Classifier=src['Classifier'],
                                                     Classifier_guid=src['Classifier_guid'], Package_ID=spk, ea_guid=guid(),
                                                     CreatedDate=ts, ModifiedDate=ts, Note=None), 'Object_ID')

        # User からのメッセージ: 番号付き手順 "N. Userは〜" の相手 (表示名がいちばん長く一致するもの)
        label = {}
        for o in order:
            r = row(c, "select o.Name, k.Name kn from t_object o join t_object k on k.Object_ID=o.Classifier where o.Object_ID=?", (o,))
            label[o] = (r['Name'] or '') + r['kn']
        user_targets = []
        for line in (scen['Notes'] or '').splitlines():
            line = line.split('//')[0]
            mm = re.match(r'\s*\d+\.\s*User(?:は|が)(.*)', line)
            if mm:
                cand = [o for o in order if label[o] in mm.group(1)]
                if cand:
                    user_targets.append(max(cand, key=lambda o: len(label[o])))
                else:
                    log.append(f'  ? User の相手が分からない: {line.strip()}')

        plan = []   # ('user', target_comm_obj) | ('msg', comm_msg)
        pending = list(user_targets)
        for m in msgs:
            while pending and m['Start_Object_ID'] == pending[0]:
                plan.append(('user', pending.pop(0)))
            plan.append(('msg', m))
        for t in pending:
            log.append(f'  ? User から {label[t]} へのメッセージを置く場所がない')

        # 戻りメッセージ: 戻り値のある同期呼び出しは、呼ばれた側が次に制御を手放すところ (呼び出しの入れ子が
        # 閉じるところ) で、その値を名前にした戻りメッセージを呼んだ側へ返す
        ACT = 'actor'
        def ends(kind, x):
            return (ACT, x) if kind == 'user' else (x['Start_Object_ID'], x['End_Object_ID'])
        with_ret, stack, replied = [], [], set()    # stack: (呼んだ側, 呼ばれた側, 戻り値)
        def unwind(sender):
            while stack and stack[-1][1] != sender:
                caller, callee, v = stack.pop()
                if v:
                    with_ret.append(('ret', (callee, caller, v)))
        for kind, x in plan:
            snd, rcv = ends(kind, x)
            unwind(snd)
            with_ret.append((kind, x))
            if kind == 'user' or (x['PDATA1'] or 'Synchronous') == 'Synchronous':
                v = re.match(r'retval=([^;]*);', (x['PDATA2'] if kind == 'msg' else '') or '')
                v = v.group(1).strip() if v else ''
                stack.append((snd, rcv, '' if v in ('', 'void') else v))
                if kind == 'msg' and v not in ('', 'void'):
                    replied.add(x['Connector_ID'])
        unwind(None)
        plan = with_ret

        # 既存メッセージ (手作業分): 向きと名前が同じものを順に使い回す。合わないものは残して報告する
        existing = [dict(r) for r in c.execute("select Connector_ID, Start_Object_ID, End_Object_ID, Name, PDATA4 from t_connector "
                                               "where DiagramID=? and Connector_Type='Sequence' order by SeqNo", (did,))]
        # 配置
        xs = {actor: FIRST_X}
        for i, o in enumerate(order):
            xs[life[o]] = FIRST_X + LIFE_PITCH * (i + 1)
        center = lambda oid: xs[oid] + LIFE_W // 2
        bottom = MSG_Y0 - MSG_DY * len(plan) - 60
        on = {r[0]: r[1] for r in c.execute("select Object_ID, ObjectStyle from t_diagramobjects where Diagram_ID=?", (did,))}
        duids = {}
        seqz = 1
        for oid in [actor] + [life[o] for o in order]:
            l = xs[oid]
            if oid in on:
                c.execute("update t_diagramobjects set RectTop=?, RectLeft=?, RectRight=?, RectBottom=? "
                          "where Diagram_ID=? and Object_ID=?", (TOP, l, l + LIFE_W, bottom, did, oid))
                duids[oid] = re.search(r'DUID=([0-9A-F]+)', on[oid]).group(1)
            else:
                du = duid(); duids[oid] = du
                insert(c, 't_diagramobjects', {'Diagram_ID': did, 'Object_ID': oid, 'RectTop': TOP, 'RectLeft': l,
                                               'RectRight': l + LIFE_W, 'RectBottom': bottom, 'Sequence': seqz,
                                               'ObjectStyle': (ACTOR_STYLE if oid == actor else OBJ_STYLE).format(du)})
            seqz += 1

        # シナリオのノート (+ ノートリンク)
        note = c.execute("select o.Object_ID from t_object o join t_diagramobjects x on x.Object_ID=o.Object_ID "
                         "where x.Diagram_ID=? and o.Object_Type='Note' and o.PDATA1='Scenario' and o.PDATA2=? and o.PDATA3=?",
                         (did, str(actor), SCENARIO)).fetchone()
        text_lines = sum(max(1, math.ceil(len(s) / 16)) for s in (SCENARIO + '\n' + (scen['Notes'] or '')).splitlines())
        nbottom = min(bottom, TOP - 8 - 15 * text_lines)
        if note:
            note = note[0]
            c.execute("update t_diagramobjects set RectTop=?, RectLeft=?, RectRight=?, RectBottom=? where Diagram_ID=? and Object_ID=?",
                      (TOP - 8, NOTE_L, NOTE_R, nbottom, did, note))
        else:
            src = row(c, "select * from t_object where Object_Type='Note' and PDATA1='Scenario' and PDATA2=? and PDATA3=? "
                         "order by Object_ID limit 1", (str(actor), SCENARIO))
            note = insert(c, 't_object', dict(src, Package_ID=spk, ea_guid=guid(), CreatedDate=ts, ModifiedDate=ts), 'Object_ID')
            du = duid()
            insert(c, 't_diagramobjects', {'Diagram_ID': did, 'Object_ID': note, 'RectTop': TOP - 8, 'RectLeft': NOTE_L,
                                           'RectRight': NOTE_R, 'RectBottom': nbottom, 'Sequence': seqz,
                                           'ObjectStyle': OBJ_STYLE.format(du)})
            nl = row(c, "select * from t_connector where Connector_Type='NoteLink' and Start_Object_ID=? and End_Object_ID=?",
                     (src['Object_ID'], actor))
            nid = insert(c, 't_connector', dict(nl, Start_Object_ID=note, ea_guid=guid()), 'Connector_ID')
            insert(c, 't_diagramlinks', {'DiagramID': did, 'ConnectorID': nid,
                                         'Geometry': 'SX=0;SY=0;EX=0;EY=0;EDGE=2;$LLB=;LLT=;LMT=;LMB=;LRT=;LRB=;IRHS=;ILHS=;',
                                         'Style': NOTELINK_STYLE.format(duids[actor], du), 'Hidden': 0})

        # メッセージ
        n_new = n_reuse = 0
        lf = lambda k: actor if k == ACT else life[k]
        n_ret = 0
        for i, (kind, x) in enumerate(plan):
            y = MSG_Y0 - MSG_DY * i
            if kind == 'user':
                s, e = actor, life[x]
                src = {'Name': None, 'PDATA1': 'Synchronous', 'PDATA2': 'retval=void;', 'StyleEx': None}
                tag = None
            elif kind == 'ret':
                s, e = lf(x[0]), lf(x[1])
                src = {'Name': x[2], 'PDATA1': 'Synchronous', 'PDATA2': None, 'StyleEx': None}
                tag = None
                n_ret += 1
            else:
                s, e = life[x['Start_Object_ID']], life[x['End_Object_ID']]
                src = dict(x, PDATA2='retval=;') if x['Connector_ID'] in replied else x
                tag = row(c, "select * from t_connectortag where ElementID=? and Property='operation_guid'", (x['Connector_ID'],))
                og = self.op_guid(x['Name'], e)
                if og:      # コミュニケーション図側の対応が古い GUID のこともあるので、名前で引き直す
                    tag = dict(tag or {'Property': 'operation_guid', 'NOTES': None}, VALUE=og)
                elif tag and not c.execute("select 1 from t_operation where ea_guid=?", (tag['VALUE'],)).fetchone():
                    tag = None
            right = center(e) >= center(s)
            is_ret = '1' if kind == 'ret' else '0'      # PDATA4 = 戻りメッセージか (EA は空だと戻り扱いにすることがある)
            pos = dict(SeqNo=i + 1, PtStartX=center(s), PtEndX=center(e), PtStartY=y, PtEndY=y,
                       Start_Edge=2 if right else 4, End_Edge=4 if right else 2, PDATA4=is_ret)
            hit = next((m for m in existing if (m['Start_Object_ID'], m['End_Object_ID']) == (s, e)
                        and (m['Name'] or '') == (src['Name'] or '')
                        and ((m['PDATA4'] or '0') == '1') == (kind == 'ret')), None)
            if hit:
                existing.remove(hit)
                mid = hit['Connector_ID']
                cur = row(c, "select PDATA2, StyleEx from t_connector where Connector_ID=?", (mid,))
                if kind == 'msg':   # 手作業で空のままの引数はコミュニケーション図から埋める。戻り値は応答メッセージ側
                    if x['Connector_ID'] in replied:
                        pos['PDATA2'] = 'retval=;'
                    if not cur['StyleEx'] and src['StyleEx']:
                        pos['StyleEx'] = src['StyleEx']
                c.execute(f"update t_connector set {','.join(k + '=?' for k in pos)} where Connector_ID=?",
                          list(pos.values()) + [mid])
                n_reuse += 1
                if tag:
                    have = row(c, "select PropertyID, VALUE from t_connectortag where ElementID=? and Property='operation_guid'", (mid,))
                    if not have:
                        insert(c, 't_connectortag', dict(tag, ElementID=mid, ea_guid=guid()), 'PropertyID')
                    elif have['VALUE'] != tag['VALUE']:
                        c.execute("update t_connectortag set VALUE=? where PropertyID=?", (tag['VALUE'], have['PropertyID']))
                continue
            m = dict(self.tmpl['message'], Name=src['Name'], Start_Object_ID=s, End_Object_ID=e,
                     PDATA1=src['PDATA1'] or 'Synchronous', PDATA2=src['PDATA2'], PDATA3='Call',
                     PDATA5=MSG_PDATA5, StyleEx=src['StyleEx'], DiagramID=did, ea_guid=guid(), Notes=None, **pos)
            mid = insert(self.con, 't_connector', m, 'Connector_ID')
            n_new += 1
            if tag:
                insert(c, 't_connectortag', dict(tag, ElementID=mid, ea_guid=guid()), 'PropertyID')
        for m in existing:
            log.append(f'  ! 対応が付かない既存メッセージを残した: {m["Connector_ID"]} {m["Name"]}')
            c.execute("update t_connector set SeqNo=? where Connector_ID=?", (len(plan) + 1, m['Connector_ID']))
        log.append(f'  lifelines {len(order)} (+User), messages {len(plan)} (returns {n_ret}; new {n_new}, reused {n_reuse}), '
                   f'User->: {[label[t] for t in user_targets]}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-m', '--model', default='分析')
    ap.add_argument('--db')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true', help='EA が起動中でも実行する')
    args = ap.parse_args()
    if not args.dry_run and not args.force and ea_running():
        sys.exit('EA が起動中。閉じてから実行すること (--force で無視)。')
    db = args.db or find_db()
    if not args.dry_run:
        bak = db + datetime.datetime.now().strftime('.bak_%Y%m%d_%H%M%S')
        shutil.copy2(db, bak)
        print('backup:', bak)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    g = Gen(con, args.model)
    log = []
    for comm_name, seq_name in SCENES.items():
        if child_pkg(con, g.comm, comm_name):
            g.scene(comm_name, seq_name, log)
        else:
            log.append(f'{seq_name}: 場面 {comm_name} がない。飛ばす')
    print('\n'.join(log))
    if args.dry_run:
        con.rollback()
        print('dry-run: 変更していない。')
    else:
        con.commit()
        print('integrity:', con.execute('pragma integrity_check').fetchone()[0])
    con.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
