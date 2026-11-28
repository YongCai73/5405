"""Chinese questions over an English record.

The transcript, the project files, the skill contracts and every matching rule in
this package are English: routing phrases, keyword retrieval, session exit phrases
and the suggestion cues all look for English words. A Chinese question would match
none of them.

Nothing in those mechanisms changes. A Chinese question is given an English *gloss*
(the English cue for each glossary phrase it contains) and the existing code runs
on ``question + gloss``. With a configured model, retrieval also asks the model for
English search keywords, because a glossary cannot know a meeting's own vocabulary.
The answer itself is written by the model in the language of the question;
quotations stay exact English substrings of the sources.
"""
import re

_CJK = re.compile(r'[㐀-鿿]')

# Chinese phrase -> the English words the existing rules look for. Longer phrases win, so
# "为什么" is read as "why" and not as "什么". Keep cues aligned with skills/*.json.
GLOSSARY = {
    # trace_decision
    '为什么': 'why', '为何': 'why', '怎么会': 'how come', '原因': 'reason', '理由': 'reason rationale',
    '决定': 'decision decided', '决策': 'decision', '定了': 'decided', '否决': 'rejected', '拒绝': 'rejected',
    '放弃': 'rejected', '没有采用': 'rejected', '选择了': 'chose', '选择': 'choose chose', '选用': 'chose',
    '采用': 'adopted chose', '同意': 'agreed',
    # compare_options
    # Bare "比较" is left out: it usually means "rather" ("比较合理"), not "compare".
    '比较一下': 'compare', '比较下': 'compare', '做个比较': 'compare', '做比较': 'compare', '比一比': 'compare', '对比': 'compare', '相比': 'compared', '区别': 'difference between', '差异': 'difference between',
    '优缺点': 'pros and cons', '利弊': 'pros and cons', '权衡': 'tradeoff', '取舍': 'tradeoff', '方案': 'proposal plan',
    '选项': 'options', '替代': 'alternatives', '哪个更好': 'better compare',
    # catch_up
    '跟上进度': 'catch up', '补一下进度': 'catch me up', '我错过了什么': 'missed', '错过': 'missed', '总结': 'summarize summary',
    '概括': 'summarize', '摘要': 'summary', '回顾': 'recap', '目前为止': 'so far', '到目前': 'so far', '发生了什么': 'what happened',
    '有什么变化': 'what changed', '最新': 'latest', '最近': 'recent', '进展': 'status update', '进度': 'status',
    # clarify
    '解释一下': 'explain', '解释': 'explain', '说明一下': 'explain', '澄清': 'clarify', '定义': 'define definition',
    '什么意思': 'what does it mean', '是什么意思': 'what does it mean', '含义': 'meaning', '通俗': 'plain language',
    '没听懂': 'do not understand', '没懂': 'do not understand', '不明白': 'do not understand', '不理解': 'do not understand',
    '不清楚': 'unclear', '搞不清': 'confused', '困惑': 'confused',
    # grill_me
    '拷问我': 'grill me', '考考我': 'quiz me', '挑战我': 'challenge me', '质疑我': 'challenge me', '挑战一下我': 'challenge me',
    '挑毛病': 'poke holes', '找漏洞': 'poke holes', '唱反调': "devil's advocate", '反驳我': 'push back',
    # design_review
    '设计评审': 'design review', '评审一下': 'design review', '可能出什么问题': 'what could go wrong',
    '会出什么问题': 'what could go wrong', '哪里会出问题': 'what could go wrong', '失败场景': 'failure scenarios',
    '风险': 'risks risk', '假设': 'assumptions', '弱点': 'weaknesses', '薄弱': 'weaknesses',
    # leaving a session
    '停止': 'stop', '结束吧': 'stop', '到此为止': 'stop', '够了': "that's enough", '不用了': 'stop', '收尾': 'wrap up',
    # follow-up references
    '它': 'it', '他们': 'they', '刚才那个': 'that earlier', '刚才': 'earlier', '之前说的': 'earlier that',
    # wording requests read by the suggestion rules
    '简短': 'briefly', '简洁': 'briefly', '简要': 'briefly', '短一点': 'shorter', '一句话': 'in one sentence',
    '不要重复': "don't repeat", '别重复': "don't repeat", '举个例子': 'with an example', '举例': 'with an example',
    '一步一步': 'step by step', '分步骤': 'step by step', '要点列表': 'bullet', '用中文': 'in chinese',
    # common meeting vocabulary, for keyword retrieval
    '谁负责': 'who owns owner', '负责人': 'owner owns', '负责': 'owns owner responsible', '归谁': 'owns owner',
    '评估': 'evaluation', '评测': 'evaluation', '预算': 'budget', '成本': 'cost', '费用': 'cost', '截止': 'deadline due',
    '期限': 'deadline due', '什么时候': 'when', '何时': 'when', '行动项': 'action items', '待办': 'action items',
    '下一步': 'next steps', '未解决': 'open questions', '遗留问题': 'open questions', '安全': 'security',
    '权限': 'access permission', '访问': 'access', '撤销': 'revocation revoke', '录音': 'recording capture audio',
    '麦克风': 'microphone', '转写': 'transcription transcript', '转录': 'transcription transcript', '语音识别': 'asr speech',
    '部署': 'deployment', '数据库': 'database', '队列': 'queue', '延迟': 'latency', '性能': 'performance',
    '测试': 'test', '检索': 'retrieval', '证据': 'evidence', '快照': 'snapshot', '记忆': 'memory', '版本': 'version',
    '技能': 'skills', '工具': 'tools', '模型': 'model', '接口': 'api', '上线': 'launch', '发布': 'launch release',
    '设计': 'design', '架构': 'architecture', '会议': 'meeting', '策略': 'policy', '重试': 'retry retries',
    '超时': 'timeout', '容量': 'capacity', '存储': 'store storage', '引用': 'citation', '回放': 'replay',
}
_ORDERED = sorted(GLOSSARY, key=len, reverse=True)

# Headings the host adds around an answer, for a question asked in Chinese.
ZH_LABELS = {
    'Insufficient evidence in this authorized snapshot.': '在当前授权范围的会议记录和文件中没有找到足够的证据。',
    'No direct evidence for this was found in the meeting record or approved files.': '会议记录和已批准的文件中没有找到这个问题的直接证据。',
    'Model inference (review against quotations)': '模型归纳（请对照原文引用核对）',
    'Explanation (model wording; check against the quotations)': '解释（模型的表述，请对照原文引用核对）',
    'Model suggestions (not meeting facts; check against the quotations)': '模型建议（不是会议事实，请对照原文引用核对）',
    'Assessment of your answer (model critique, not a meeting fact)': '对你回答的评价（模型的点评，不是会议事实）',
    'Exact supporting quotations': '原文引用',
    'Beyond the meeting record (model knowledge and reasoning; NOT from the meeting or project files, not verified)':
        '会议记录之外（模型自身的知识与推理；并非来自会议或项目文件，未经核实）',
    'General background (model knowledge; NOT from the meeting or project files, not verified)':
        '通用背景（模型知识；并非来自会议或项目文件，未经核实）',
    'Challenge {round} of {rounds}': '第 {round}/{rounds} 轮追问',
    'Challenge session closed.': '追问环节已结束。',
}

KEYWORD_PROMPT = ('The user asks a question in Chinese about a meeting whose transcript and project files are in English. '
                  'Reply with 3 to 12 English search keywords for finding the relevant passages: the key nouns, names and verbs '
                  'of the question, with likely English synonyms. Lower case, separated by spaces, nothing else. '
                  'Do not answer the question.')


def has_cjk(text):
    return bool(_CJK.search(text or ''))


def gloss(text):
    """The English cues for the glossary phrases found in `text`, longest phrase first."""
    if not has_cjk(text):
        return ''
    remaining = text; found = []
    for phrase in _ORDERED:
        if phrase in remaining:
            found.append(GLOSSARY[phrase])
            # Blank it so a shorter phrase inside it ("什么" in "为什么") is not counted as well.
            remaining = remaining.replace(phrase, ' ')
    return ' '.join(dict.fromkeys(found))


def bridged(text):
    """`text` followed by its English gloss; unchanged when it has no Chinese."""
    extra = gloss(text)
    return text + ' ' + extra if extra else text


def clean_keywords(reply, limit=16):
    """English keywords from a model reply: ASCII words only, bounded, never instructions."""
    words = re.findall(r'[a-z0-9][a-z0-9_-]{1,39}', (reply or '').lower())
    return ' '.join(list(dict.fromkeys(words))[:limit])


def labeller(question):
    """Maps a host heading to Chinese when the question is in Chinese."""
    if not has_cjk(question):
        return lambda text: text
    return lambda text: ZH_LABELS.get(text, text)
