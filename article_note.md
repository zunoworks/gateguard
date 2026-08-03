# AIが消すのは、いつも「未コミットの作業」だ — blast radiusを測ってから壊させる

## 2026年、事故は「攻撃」ではなく「日常作業」から起きている

今年Xで拡散したAIコーディング事故は、経路こそ違えど同じ形をしている。

- 許可されたTerraform操作で本番DBが消え、**自動スナップショットまで道連れ**になった（数年分の提出データが数秒で消滅）
- 未ステージ・未コミットの5ファイルが「gone for good」——git履歴に何も残っていなかった
- writeモードで開いたファイルの生成が途中で失敗し、**中身が空**になった。未コミットだったので戻せない

共通項は攻撃者ではない。**消えたものが「未コミット・未追跡」だった**ことだ。追跡済みのファイルはgit履歴から戻せる。未コミットの作業は、消えたら世界のどこにも存在しない。事故の痛みはほぼ全部ここに集中している。

[GateGuard](https://github.com/zunoworks/gateguard)はこの一点——**未コミットのデータが消える直前**——に摩擦を集中させるClaude Code用フックゲートだ。v0.7.0で、単なる「止める壁」から「保険と証跡」に作り変えた。この記事はその設計と、開発中に見つけた自分自身のバグの話をする。

## 告白：うちのゲートには6バージョン穴が開いていた

v0.7.0の開発中、敵対的監査（悪魔の代弁者レビュー）で自分たちの破壊コマンド検知を叩いたら、こうなった。

```
MATCH rm -rf x
miss  rm -fr x          ← フラグの順番が違うだけ
miss  rm -r build
miss  dd if=/dev/zero of=/dev/sda
miss  git checkout -- file.txt
miss  git clean -fd
```

原因は正規表現の末尾に置いた`\b`（単語境界）1文字。`=/`や`-- `のような「非単語文字＋非単語文字」の並びでは境界が成立せず、選択肢が静かに死ぬ。**v0.1からv0.6.2まで、6バージョンの間、「破壊コマンドは常に止める」という看板の裏で主要な変種が素通りしていた。**

ここから得た結論は「もっと正規表現を頑張る」ではない。

**パターン検知は必ず漏れる。だから最後の砦をパターンに置いてはいけない。**

## v0.7.0の設計：疑って、測って、保険をかけて、記録する

### 1. ゲート自身が影響範囲を実測する（blast recon）

従来のゲートはAIに「何が消えるか列挙しろ」と要求した。だがその申告が正しいか、ゲートには分からない。v0.7.0はゲートが先に自分で測る。

```
GateGuard measured the blast radius itself:
- Contents: 1247 file(s), 3.2 MB
- ⚠ 3 file(s) exist ONLY in the working tree
  (untracked/modified — no git history has them)
```

肝は「ONLY in the working tree」の数字だ。追跡済み・クリーンなファイルはgit履歴から戻せる。**未コミットの変更と未追跡ファイルは、消えたら世界のどこにも存在しない。** 2026年の事故が失ったのは、まさにこれだ。

### 2. 検証済み保険（verified insurance）

破壊コマンドは1回目でdenyされ、事実提示の儀式を要求される。ここまでは従来通り。変わったのはリトライだ。

リトライは、**gitスナップショットを取得し、これから消えるファイルがその中に実在することを検証できた場合のみ**通る。スナップショットは一時indexで作る本物のcommit（`refs/gateguard/snapshots/`配下。ユーザーのindex・HEAD・作業ファイルには一切触れない）。復元はワンコマンドで、監査証跡に記録される。

```
git restore --source=<commit> --worktree -- .
```

「バックアップを取った」ではなく「壊すものが入っていることを確認した」。検証に失敗したら従来通りdenyのまま（fail-closed）。保険が空振りする余地を構造的に消してある。

`/rewind`やcheckpoint系ツールとの違いもここにある。checkpointが戻せるのは**エージェントが編集ツール経由で行った変更**だ。現場で実際に消えているのは、**シェルコマンドが破壊した未コミット・未追跡ファイル**——checkpointもgit履歴も持っていない状態で、事前に取った検証済みスナップショットだけが持っている。Writeによる全文上書き（生成途中で失敗してファイルが空になる事故クラス）も同様に、上書き直前の内容をgit blobに退避してから通す。

### 3. 迂回路は祈らずに塞ぐ

denyだけの壁は、モデルに迂回を学習させる。既知のルートは実装で塞いだ。

- **スクリプト密輸**：`bash cleanup.sh`のようにファイルを実行するコマンドは、**スクリプトの中身**を同じ破壊パターンでスキャンしてから通す
- **言語内削除**：`python -c "shutil.rmtree(...)"`、`rimraf`、`fs.rmSync`、`find -delete`も破壊パターンに
- **deny-listの隙間**：`rm -rf`を禁止しても`rm -fr`（フラグ順違い）、`git clean -fd`、`terraform destroy`は文字列マッチを素通りする。現場で実際に報告されている抜け道は全部パターンに入れた
- **ゲート自身の無効化**：`.gateguard.yml`への編集はhigh-risk扱い（証拠による免除なし・ユーザーの明示指示を要求）。シェル経由（`>`リダイレクト、`sed -i`、`rm`）も破壊ゲート対象

なお、これは**permission promptではなくhook**なので、自律性のために`--dangerously-skip-permissions`で走らせていてもゲートは生きている。プロンプト連打の摩擦を嫌ってpermissionを切る使い方と、破壊の縁だけ守る設計は両立する。

### 4. フライトレコーダー（改ざん検知つき監査証跡）

全ゲート判断はハッシュチェーンでつながったJSONLに記録される。1行でも書き換え・削除すれば`gateguard audit --verify`が該当行と理由を特定する。さらに：

- 観測された調査行動（Read/Grep/Glob）も同じ証跡に流れ、証拠パスの記録には**それを正当化した調査エントリが紐付く**。「AIが引き金を引いた時、何を知っていたか」に答えられる
- チェーンだけでは「丸ごと再生成」は検知できない（秘密鍵がないので）。`gateguard anchor --push origin`がチェーン先頭をgit refとしてリモートに固定し、全面書き換えを照合不一致として検出する

```
$ gateguard audit
chain: VERIFIED — 214 chained record(s)
12:01:15  allow  Edit  evidence_pass       [justified by: read auth.py; grep src]
12:03:40  DENY   Bash  fact_force_destructive  [blast: 1247 files, 3 unbacked]
12:04:02  allow  Bash  destructive_insured [INSURED snapshot=... verified=True]
```

## 「ウザくないの？」

摩擦は消すものではなく配置するものだ。行儀のいいモデルには、調査の観測（evidence pass）・読み取り専用コマンドの素通し・検証済みディレクトリの30分パスで、ゲートはほぼ無音になる。実測（PainBench, Opus 5）でもgated armのdenyは全タスク通して1回だった。

摩擦が集中するのは破壊の縁、それも1往復だけ。そして残る摩擦は`gateguard stats`で監視できる——denyの分布は、そのままウザさの分布だ。

## 導入は2コマンドのまま

```bash
pip install gateguard-ai
gateguard init
```

## 正直な限界

- スナップショットが守れるのはgit worktreeの中だけ。DB・リモート・リポジトリ外は保険対象外（ゲートはそう明言してdenyし続ける）
- スクリプト偵察はインタプリタ直後の第1引数のみ（`bash -e run.sh`は漏れる）
- ローカルアンカーはリポジトリアクセス権があれば消せる。本気の保全は`--push`でリモートへ

パターンは漏れる。自己申告は信用できない。だから、観測して、測って、保険をかけて、記録する。

**GitHub:** https://github.com/zunoworks/gateguard
**PyPI:** `pip install gateguard-ai`

---

*ZUNO WORKS K.K.*
