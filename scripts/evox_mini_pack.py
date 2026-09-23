# Mini-slice of EvoX experiment 1 (ZebraLogic + MATH + UGPhysics).
# Source: https://evomap.ai/zh/blog/how-ai-swarms-win-from-26-to-71-percent
# Full paper set is 563 items; this is 6 atomic parts + 1 programmatic merge.

PROJECT_ID = 'proj_evox_mini2'
PROJECT_NAME = 'evox-mini-6plus1'

ROOT_PROMPT = (
    'EvoX-style mini benchmark (6 independent items + merge). '
    'Do NOT solve in one context. Each part is a separate board task.'
)

PARTS = [
    {
        'task_id': 'evox_p1_zebra',
        'target_at_name': 'bibo',
        'prompt': (
            'ZebraLogic-style unique grid. Three people Amy, Bob, Cal live in '
            'houses 1,2,3 from left to right. Colors are red, green, blue '
            '(one each).\n'
            'Clues:\n'
            '1. Bob lives in house 1.\n'
            '2. The green house is immediately to the right of the red house.\n'
            '3. Amy lives in the red house.\n'
            'Question: who lives in the blue house?\n'
            'Reply with the person name only.'
        ),
        'gold': 'Bob',
    },
    {
        'task_id': 'evox_p2_sumsq',
        'target_at_name': 'lily',
        'prompt': (
            'MATH (algebra). Compute the exact integer value of '
            '1^2 + 2^2 + ... + 10^2.\n'
            'Reply with the integer only.'
        ),
        'gold': '385',
    },
    {
        'task_id': 'evox_p3_mod',
        'target_at_name': 'bibo',
        'prompt': (
            'MATH (number theory). What is the remainder when 2^10 is divided '
            'by 10?\nReply with the remainder only.'
        ),
        'gold': '4',
    },
    {
        'task_id': 'evox_p4_ident',
        'target_at_name': 'lily',
        'prompt': (
            'MATH (algebra). If x + 1/x = 3 and x ≠ 0, compute '
            'x^2 + 1/x^2.\nReply with the integer only.'
        ),
        'gold': '7',
    },
    {
        'task_id': 'evox_p5_ke',
        'target_at_name': 'bibo',
        'prompt': (
            'UGPhysics (mechanics). A 2 kg mass moves at 3 m/s. What is its '
            'kinetic energy in joules? Use KE = 1/2 m v^2.\n'
            'Reply with the number only.'
        ),
        'gold': '9',
    },
    {
        'task_id': 'evox_p6_pe',
        'target_at_name': 'lily',
        'prompt': (
            'UGPhysics (mechanics). Take g = 10 m/s^2. A 2 kg mass is held '
            '5 m above the ground. What is its gravitational potential energy '
            'in joules? Use PE = m g h.\nReply with the number only.'
        ),
        'gold': '100',
    },
]

MERGE = {
    'task_id': 'evox_merge',
    'target_at_name': 'ms_agent',
    'blocked_by': [p['task_id'] for p in PARTS],
    'prompt': (
        'Program-style merge (do not re-solve). Read teammates via '
        'dispatch_result_read / the board. Output one JSON object with keys '
        'p1..p6 and string values (the collected answers). No extra prose.'
    ),
}
