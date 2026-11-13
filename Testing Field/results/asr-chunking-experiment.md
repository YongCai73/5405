\# Live ASR experiment: chunk length and VAD (Zimo Li, 2026-11-12)



Setup: Windows laptop, built-in Realtek microphone (`pyaudio:9`), CPU only, 60 s recordings, English script read aloud, Claude Haiku for `catch\_up`.



\## Changes (branch `zimo/asr-chunk-length`)



1\. `audio.py` chunk length 3 s → 10 s.

2\. `audio.py` `vad\_filter` False → True (upstream Meet2Notes default is True).



\## Results



| Setting | ASR model | Accuracy (plain script) | Hallucination on silence | Sentences cut at chunk edge | Processing lag p95 | Worst-case wait (chunk + processing) |

|---|---|---|---|---|---|---|

| 3 s, VAD off (prototype) | base.en | poor; technical terms wrong | yes ("good night", "James?") | often | 4.0 s | \~7 s |

| 3 s, VAD off | small.en | slightly better; terms still wrong | yes | often | not recorded | — |

| 10 s, VAD off | small.en | 3 of 4 sentences correct | not observed | sometimes | not recorded | — |

| 10 s, VAD on | small.en | nearly all correct | none in 30 s of silence | sometimes | 6.1 s | \~16 s |



Technical terms (SQLite, Kafka) were misrecognized in every 3 s run with both models.



\## Takeaways



\- Short 3 s chunks cut sentences and remove context; this hurt accuracy more than model size.

\- VAD removes the silence hallucinations that previously filled the `catch\_up` window.

\- Cost: worst-case wait rises from \~7 s to \~16 s. Part of that comes from small.en, which is slower than base.en.

\- Zero dropped segments or errors; the audio queue never exceeded 1 of 4.



\## Not yet tested / next steps



\- base.en with 10 s + VAD, to separate the effect of the model from the effect of chunking.

\- Cutting chunks at pauses instead of fixed 10 s boundaries.

\- Vocabulary prompt for technical terms.

\- 20-minute run and microphone unplug test.

