"""Which table of identity size factors each commit ran with (data only).

Generated on 2026-10-16, and again on 2026-10-17 (P1-B.3, and after the P1-A
freeze at 2608230632365c), from every branch, tag and remote of the repository:

    git rev-list --branches --tags --remotes                      # every commit
    git rev-list --branches --tags --remotes --ancestry-path ^784c2bd6a732
                                                                  # descendants of v7b
    (plus 784c2bd6a732 itself)
    git show <sha>:backend/trading/orchestration/persistence.py   # per descendant:
        "self._pipeline.with_decision_recorder(" present or not

and abbreviated to 12 hex digits (no two of these commits share a prefix). A
regeneration only adds: a listed commit is never dropped.

  * ``PRE_V7B_COMMITS``: the commits that do not contain 784c2bd6a732
    ("feat(bridge): v7b: the four sendable identities, data rows only"). Every
    commit that recorded decisions on the live system is one of them (88b963d,
    904af38 and 664c93b, the v7a head live since 2026-10-16).
  * ``V7B_COMMITS``: 784c2bd6a732 and those descendants whose repository runner
    rebuilds the pipeline without its identity factor (every decision x1).
  * ``V7B_SIZED_COMMITS``: the descendants whose runner keeps it
    (``with_decision_recorder``: v7b.1 7f7178d4f90f and its descendants, and
    T-8's own copy of the fix, ded4264f61c9), so the four rows are applied:
    the v7b verdict's head 7872c86 and its docs-only children up to the frozen
    bridge-v7b tip 5d0eb1c, and the P1-A and P1-B heads (P1-A to its frozen tip
    2608230, P1-A.4 4c5c6c6 and P1-A.5 a6c8d67 included; P1-B to its merge of
    that tip, 89ce62d).

``trading.research.size_history`` maps each to its table version.

The source is the run's ``source_sha``: ``git rev-parse HEAD`` of the checkout
it ran from (``trading.operations.shadow_service.source_sha``), ``-dirty`` with
local changes. A commit that is not listed reads as unknown and is named in the
nightly evidence, never guessed; listing one is a data-only change (rerun the
commands above). A commit cannot list itself, so a head made after this file
(a later docs/reviews commit included) is unknown here until a later
regeneration lists it. From the decision-sizing sidecar
(``trading.storage.decision_sizing``, T-8) on, every decision records its own
effective multiplier and needs no row; a run with no decision still reads its
commit here.
"""
from __future__ import annotations

PRE_V7B_COMMITS: frozenset[str] = frozenset(
    """
    00534df202b2 016fa28049f2 017b3bb5e451 033ca627425c 03e9ad9a108c 042a689102a6
    044520f8011b 045b8ccbd2ca 04817958892b 04ed0fab14d8 059e742a5553 074faee851e2
    082f23a6abf7 08440ad4fc94 084b13943371 088e2c16cf77 08ed5315817f 093bd833875a
    09fa76fa546e 0a0d88cec52c 0bde712c01f3 0c0389468e05 0c0533dab264 0cd8d5b1eb81
    0d3d4fac7599 0d483b6c1bb7 0e12e6d99c79 0e3eb3ece684 0ebd2de05905 0ef6b3035e3e
    0f5afc494289 10ad9cd8fcca 10b0c1b677f7 11d489134854 15525e3989c0 1591cd70f288
    15ad47a8c4d3 16251abf697b 16280057ab04 163c27b76119 16afb84026db 171d2ba90abf
    1744bdb94e0f 174c7014df0e 1819035f0412 184df08b37af 196bd9f55801 1982fba431ae
    19cedb87508e 1a5182a650a1 1a8acc7c3e46 1c211a4f9c81 1c9455f14740 1d2b876586f5
    1e39bd6eb01e 1e633a213549 1e6529f80794 1e8f42094371 1f5ce369a55c 1f867c380630
    2105c314995b 21363b7d46cf 2156d5ae9ad9 21aa8f326f57 21c00d3fa38b 21e59e4d749a
    22316e56e707 22d6c8599ffc 2314346d4adb 233d4719f10b 2365f737feb2 23aa2405d979
    23da9fe73e0e 2479a6e95e60 2484bc34e773 24b694d305de 24cdf13459ca 24fd0f5523be
    251c4be41177 2534618fff5c 2539239d2af9 26d6bc47da62 272a667b4579 27d606cab39c
    28ed1d0db361 2970bb0a82e4 297f28ab336c 29b8b4a0ab35 2a5c1dc5b03b 2a5e48c976a1
    2aa8deffe3df 2b17189ea171 2b4db50101b6 2b81eeeb39b5 2d276047faa1 2d9ba7cee956
    2de0bd2e3408 2f81354a7e9a 3059b2f843d5 307160cf8bf3 329d0c73c0dc 3354c3c829b0
    342c2691b49d 355c70cfa75a 3561c0e002f6 3579e92ea92c 357fae2968f3 360c973bdf32
    36759824bff7 3699fbf57b05 374df0eb1d78 375cadc0fe54 3769d7ca9290 377c45e18a15
    377f4b17dd18 37ceb2335818 382ad9ecfe7c 385f79313c8b 38f61dda96d4 39c5ee793e71
    3a29a8519df2 3a2dcb7f78cf 3a4acf07ef16 3cc8ad1600fd 3cdb0a72c113 3d0437bb6328
    3d0f42c76042 3d19d800518c 3d300dcc6fbe 3d6be6df4d16 3e39594e3115 3e4832a7256a
    3f714e94018a 3f71a8f9abb8 3fff9846300b 404b4a32ae3a 40b0d65c9d59 4104ccabc5fc
    42be1fcf24d2 4383b7b8bad2 442722d13417 4470210e84a0 44fcb9376b08 452557fcf3f1
    456330e95b64 470d3891d1cd 471bde449d51 4770d3e95e77 491def8a6c15 4a60de6efb45
    4a7afd66e5c8 4acf8b29ffb3 4b73ae639589 4b9d50b6b182 4c755f6bdbfb 4c8a79b1af2f
    4c9ff7aff5b9 4cada4cc5003 4d08290af0d3 4d387050b14b 4d506e3402e6 4d8bbda2c7bb
    4d8db93b60e4 4f1e892fe62c 4f5473459585 519af080ea4c 5275373ac96e 52a5f49544c7
    53063f156f96 5330d71285cf 5484356f4ce3 54b0c7a653f1 553dcb7f19ac 556738475670
    5682ec53e42f 583ec0b58728 5840d92d6472 586680082846 58e6d1ed38f3 58faa0f2e2b7
    593bc337e083 59fba4ddae7e 5a903434b562 5af797389d28 5b40da01f2e5 5b51b4a6e747
    5b555a6f8b24 5b7d02de3d84 5bc95e43b082 5bd0d612e5e2 5bea938f14d1 5caa5b99142b
    5ccde59f9775 5cd611b69e3b 5d03e33a4936 5d096069053d 5d0fe0088252 5d317fd8b4a9
    5f524837f554 6093f0f7d5f6 60b8d40b6020 614272cddf84 6234fec54619 624e305fcd20
    62b12584ac11 6332dd95f57e 63c1fb44c2c9 645760fdab55 645f7e674d70 646166aed8fd
    64d6c08398a5 65987a89e99c 65ac0a50aefa 65de9ce90bec 664c93be7a48 678737e5c8be
    67e374a3051c 68119ce63e3e 6882084390f4 68e47d90c4ce 692b6d747f74 694cc1a4b2f3
    6a6e8fcd40ea 6ad5923c8654 6bad52fda4e1 6cd71d2f8a15 6cec3de464ca 6f6d2a7f34c5
    6fb851ebe653 70d9a00e2abb 71f3728b2b0e 72135b16c0d8 726b97084d21 7354b894b459
    73d9491ac4cc 74b6439172db 750059fa1f76 7527b66c6525 766173307d4e 76f62f0c4bf2
    778c76003b4b 77f58c76de61 7836f2f52d44 7837375afbac 7a4e76fd285f 7a5a1ec32a79
    7b0cde3dd6fb 7b421d7bdfbb 7b5c4a82f111 7b7dc308fd90 7b931914421f 7b9614bb081a
    7d9c06661a41 7dc0bb21af24 7e261ccf2144 7e72075653f6 7ed8f962a647 7eec1efcca77
    8002869a598e 81b8c1a5ad73 81edb989916a 820fd7cd17ef 829bf8bf0d03 829e45021119
    83921ab842bd 83c1c33652a6 83c23b9f2047 845c874cc97a 8467c4f2f3c3 8516b74d85e1
    8521bb574384 8568eb463105 859b4a9b159a 85be0e2061d0 85f77d783ab3 86c6cd63e3ea
    870726a7220c 87a4bd071af3 87c974692f2f 87f812a67ac1 880d2b256880 8874e988c491
    88b963d0c155 88c325532e75 88e8ccf96cba 896d597bafdb 8982919ea4a7 89925a6c9a67
    89a42fc1facd 89f415962e4e 8a4f0f32c2b8 8aac90dd9fd5 8c53374b119f 8cab177ed2d7
    8cd7ed202854 8cfce8cab69e 8d12003d973d 8d20de50dff7 8e46ed0c2c51 8e873a9efacb
    8ea677ee9f03 8ebab676f6cb 8fbfdc8272cd 8fff843c907c 90236f8c2a4a 904af3854602
    90b4376a97c8 917e3c62f9f3 92d486b0749c 92d9990e04ce 92fc9713d183 93af72581e9d
    9628a7eebf9f 96f797c777fa 9717bd02ce22 97253c1e89dc 9733e0e505c9 984d3f9ebf36
    98dfa3e8f175 994c4c1fd77f 9b5e797ce7cf 9bba2226cd14 9c25e62a7a96 9c2de1df3cc8
    9c7962bb1bec 9c8ef03c4d57 9c91bb2b6765 9d0ce9889888 9d96919609ac 9d9c8043e75a
    9d9cd67474e9 9e1a63845813 9ea937db74ac 9f12842c96d5 9f252bcc4acc a067f9d5beb6
    a10eea592019 a28e5a5dcd4d a312a78ec9c3 a38fe7811149 a410baeb712f a428bdc61663
    a43b0296595e a4ae450e3f99 a4f36bc045f0 a537cf983eaa a549705d17d7 a5852254320b
    a61c32e218b5 a6df28015851 a701d04817b5 a70d32289759 a74c25f59875 a7e0fc5039a1
    a988d5dde829 aa475fad61d1 aa6537b8e8d5 aa7319610533 aad9b19c1fbc ab5a2a7d3705
    ac5acdcc01de ad5360d72ba6 ad876025e8b6 ad90579dffa4 adf5340ffaee ae05a4bece42
    ae0ec4d25fdb ae2c4f041b26 ae7482de6650 af74ecb09e5c b19d7cc7cfa2 b230af37165c
    b2c01ddf8892 b31e7382be69 b3b4604da9ec b3e8f9ad784e b4415a2749ff b45eb3bf0a5a
    b46d8ae9efc2 b4f1cf681bcb b5aba8e5d617 b612128f0db0 b66cc4ef7459 b73d366cb7ae
    b79238ed9d33 b7bb39951432 b80c4420abfd b81cf9b61107 b85e325b6bd2 b87ee4d89fea
    b89d4b9d64b8 b8d12c055ba3 b939a28f1fdf b956db24fe58 b99467590ee1 ba6937eb13e1
    ba9a65459965 bacf7bfe37f0 bb06fd6d8065 bb53bbcb1c17 bb7590924bc0 bcc56636881a
    bd5e3fc4438b bd9e3ee0d52c be383e0913ba be4b44f2ad8c bf94631646cf c0127802653c
    c15aafb3b918 c274f5ce99d1 c284770f3cfe c2a717697702 c30b57b11cc8 c41fd8150850
    c45aa26dbc8c c696e7ca34a1 c7d29c6824d1 c8413908a539 c850786a209b c9261adce929
    ca1d59f701b6 ca5800bdee6d ca5d45cb6cd4 caf1d86958d7 cbae81257f31 cbc8c0f09aae
    cbd8d334832f cbfce7c7b217 cc716d0fc6d1 cc7a3babf194 ccb541cfd054 ccf64eb1ff6a
    cd1135b8fdca cd8d7f205e87 ce1c77425785 ce253dd4109a ceb9a9f7aecf cf5f52edd17b
    d0940ca9baba d104372e89f1 d1d448e5afdd d25b8009b0b8 d29bad8474d2 d2fd3e7c43d6
    d3411308422e d3f1e373cbe5 d403e91830ef d45395a1295c d4707805cf62 d49d044d3549
    d4ad66e3965a d4bad69c99ae d56e7082fd44 d58ebe305d28 d5a4a5a41df8 d65172473ac7
    d67da67bd6a5 d6c4b87ddeb5 d6cf98be7644 d7299337b07c d73f23d8fa12 d8ae2017d1e2
    d9287e7fbae3 d9f366ede7a5 dcc2df3b12bd dcc9a8dd1bef dd5c4aefa0ba de380a163dff
    df082064fe5b df9c0c21ef7d dfaa4630df51 dfb6d4cc3cb8 dfd6f5495f62 e02771d3443d
    e099ca500542 e0aeb7f909c8 e0e6d6fdfa10 e0e87f19c1c0 e14b916c0a7e e15356588711
    e17eb8f92b19 e1ea9878c427 e1f42bdcff82 e2ab37b19bb1 e2bd9cb9e252 e2e5c85d0ff8
    e452401ea871 e486e51ac697 e52963a70f6e e56b2521ae49 e5997afd9989 e599b6145c27
    e69c12709b2e e6cfe85f16f0 e6e3f4e29c73 e6f65dfaccb1 e839e5d024f0 e89476cafcdf
    e8decc627ab6 e91be61ae89a e92b9b26331d e93a890ed369 e9d409270bdb e9ea6de6f43c
    e9f501cc72bd ea0404c1fdbc ea92035d3a5e eb0466bfb73b ebb2a18247d5 ebcc14f47bf0
    ebee6459668d ec72d31b5a39 ec7eebf974af ed0f76128e8c eecc958a8acd eedbe99f315a
    efd160b70e12 effc48c7f646 f09ea2b03af6 f16fbaab31df f19f0e1a22dd f2da36f053ea
    f33545ff3f4b f359225ceafa f3ae29f7e549 f52d3cb239c8 f55ee4a0ee64 f5e7c036cfb5
    f5e8bf924522 f665ffc8d171 f735d21fcd90 f76d7c4a1d65 f77023a9a14f f7a6a60c1554
    f7b471e3785d f7e0726681e1 f7ecabcc71e3 f8bca18dd594 f94d4150b430 f98c7f5a1811
    fa06f5e1dba6 fa3dd083e5e4 fa97c808a111 faabe81a255f fac16713581f faf5783a3970
    fb0c7e4a0d8b fb3941c35345 fb53495813a5 fb7f4ff3d9ed fbae8a6e0380 fbeca76cb956
    fc1bf431174a fc785ee1da66 fcd899e04d67 fe035fdcdc42 ffc28ad54782
    """.split()
)
V7B_COMMITS: frozenset[str] = frozenset(
    """
    04ad69411810 1120b7549b24 227b8b3d4fe5 299d6e13ac92 35250bcfc27a 378ebd4af372
    784c2bd6a732 78e7d0396cfd 7adcd12ede74 8399d08256ca 866bca88b16b 927bb266a906
    967cbc01e426 a8cba98cce99 bdc05a07bcef bfb1cb1259f4 d0553725d5a1 e0e5ae6a4bd7
    f8df19bf7031
    """.split()
)
V7B_SIZED_COMMITS: frozenset[str] = frozenset(
    """
    0633efdbeacd 087e9f0f4b78 09e997b93eeb 0a4893e47c8c 0b8b2b92526e 0e52f7813260
    134bde4ff2ff 201172c8e6c8 260823063236 282408a3081e 3375b7dcaba4 33acc6a5ee8b
    3a17ed29672f 3a4254e90dcb 4c5c6c61125e 5b4c45bfcb06 5d0eb1ca947a 622af486377a
    6578feb64aab 6ba575a013c7 74025d798ca3 7872c86357ee 7f7178d4f90f 809142ca60bd
    83a7f2f70a94 89ce62d21c38 8dac16b34b16 95e7534eeb70 9c3a4dcb43e3 a07084867dcf
    a23f6d43e918 a6c8d6711394 aad915741067 b2a8f8bc868b d6b168babae0 ded4264f61c9
    df6ce5983bb0 edb46c001a4b eededf9204f3 f31543030909 f656cba7a43b ff45ec8abb37
    """.split()
)
# Pinned so an edit of the lists is visible (tests/test_size_history.py).
PRE_V7B_COUNT = 557
V7B_COUNT = 19
V7B_SIZED_COUNT = 42

__all__ = [
    "PRE_V7B_COMMITS",
    "PRE_V7B_COUNT",
    "V7B_COMMITS",
    "V7B_COUNT",
    "V7B_SIZED_COMMITS",
    "V7B_SIZED_COUNT",
]
