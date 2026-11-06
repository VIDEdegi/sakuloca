"""Exact, local sound-name synonyms; never rewrites arbitrary speech content.

The audio encoder's Japanese/English alignment varies by sound. Common short
Japanese sound names can use an equivalent English label, with the applied
label returned to the UI. No filename, inferred transcript or network is used.
"""
import unicodedata

_LABELS = [
 ('A dog barking', ('犬','いぬ','イヌ','犬が吠える','犬が吠えている')),
 ('A cat meowing', ('猫','ねこ','ネコ','猫が鳴く','猫が鳴いている')),
 ('Birds chirping', ('鳥','小鳥','野鳥','鳥のさえずり','鳥がさえずる')),
 ('Rain falling', ('雨','雨が降る','雨が降っている','雨降り','降雨')),
 ('Ocean waves', ('波','海の波','海','波打ち際','波が打ち寄せる')),
 ('Wind blowing', ('風','風が吹く','風が吹いている','強風')),
 ('Thunderstorm', ('雷','かみなり','雷鳴','雷雨')),
 ('Fire crackling', ('火','焚き火','たき火','焚火','火がぱちぱち燃える','燃える火')),
 ('Crickets chirping', ('コオロギ','こおろぎ','虫','鈴虫','スズムシ')),
 ('An alarm clock ringing', ('目覚まし時計','目覚まし','めざまし','アラーム')),
 ('Typing on a keyboard', ('キーボード','キーボードを打つ','タイピング','キー入力')),
 ('An emergency siren', ('サイレン','救急車のサイレン','警報サイレン')),
 ('A car horn honking', ('クラクション','車のクラクション','自動車のクラクション')),
 ('An engine running', ('エンジン','エンジンが動く','エンジンがかかる')),
 ('A helicopter flying', ('ヘリコプター','ヘリ','ヘリコプターが飛ぶ')),
 ('A train moving', ('列車','電車','列車が走る','電車が走る','走る列車')),
 ('A steam train whistle', ('蒸気機関車','汽笛','蒸気機関車の汽笛','汽笛を鳴らす蒸気機関車')),
 ('A vacuum cleaner running', ('掃除機','掃除機をかける')),
 ('Church bells ringing', ('教会の鐘','鐘','鐘が鳴る','ベルが鳴る')),
 ('People clapping', ('拍手','手拍子','人が拍手する')),
 ('People laughing', ('笑い声','人の笑い声','笑う人','人が笑う')),
 ('Piano music', ('ピアノ','ピアノを弾く','ピアノが鳴る')),
 ('A trumpet playing music', ('トランペット','トランペットを吹く')),
 ('Jazz music', ('ジャズ','ジャズミュージック')),
 ('Drum and bass music', ('ドラムとベース','ドラムンベース','ドラムとベースの音楽')),
 ('Orchestral music', ('オーケストラ','管弦楽','管弦楽団')),
 ('A person whistling', ('口笛','人の口笛','人が口笛を吹く')),
 ('A baby crying', ('赤ちゃん','赤ちゃんの泣き声','赤ちゃんが泣く')),
 ('A rooster crowing', ('鶏','にわとり','ニワトリ','鶏が鳴く')),
 ('A person coughing', ('咳','せき','咳払い')),
 ('A person sneezing', ('くしゃみ','人がくしゃみをする')),
 ('A person snoring', ('いびき','寝息')),
 ('Footsteps', ('足音','歩く足音','人が歩く')),
 ('A door opening and closing', ('ドア','扉','ドアの開閉','扉の開閉')),
 ('A door knocking', ('ノック','ドアのノック','ドアをノックする')),
 ('Running water', ('流水','水が流れる','水道','蛇口')),
 ('A toilet flushing', ('トイレを流す','トイレの水')),
 ('A clock ticking', ('時計','時計の秒針','秒針','時計の針')),
 ('A telephone ringing', ('電話','電話の着信','着信','電話が鳴る')),
 ('Glass breaking', ('ガラスが割れる','ガラスの割れる音','割れたガラス')),
 ('A chainsaw running', ('チェーンソー','チェンソー')),
 ('A person playing guitar', ('ギター','ギターを弾く')),
 ('A violin playing music', ('バイオリン','ヴァイオリン')),
 ('Drums playing', ('ドラム','太鼓','打楽器')),
 ('A flute playing music', ('フルート','笛')),
 ('A person singing', ('歌','歌声','歌唱','人が歌う')),
]
_SOUND_ENDINGS = ('', 'の音', '音', 'の声', 'の鳴き声', 'のサウンド')
_MUSIC_ENDINGS = ('', 'の音', '音', 'の演奏', 'の音楽', 'のサウンド')


def _key(text):
    return ''.join(unicodedata.normalize('NFKC', text).casefold().split()).strip('。.!！?？')


_ALIASES = {
    _key(alias + ending): label
    for label, aliases in _LABELS
    for alias in aliases
    for ending in (_MUSIC_ENDINGS if 'music' in label.lower() or label in ('A person playing guitar', 'Drums playing', 'A person singing') else _SOUND_ENDINGS)
}


def resolve_audio_query(text):
    """Return an exact equivalent label, otherwise preserve the original text."""
    text = text.strip()
    return _ALIASES.get(_key(text), text)
