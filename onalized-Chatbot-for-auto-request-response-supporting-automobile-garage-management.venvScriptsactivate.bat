[33mb42970a[m[33m ([m[1;36mHEAD[m[33m -> [m[1;32mdemo_v2[m[33m, [m[1;31morigin/demo_v2[m[33m)[m feat: add history summary and fix the compose node to call LLM only with multi-intentand messy  messages
[33md3f4e2a[m fix: the appointment agent now has four tools to work with, including create, reschedule, list, and get. The appointment table now has another two columns start_time and date which the LLM can deduce itself, saving the employees' time
[33m7d5cbf7[m[33m ([m[1;32mdemo[m[33m)[m fix: the system can now remember the previous appointment
[33m926ab13[m[33m ([m[1;31morigin/demo[m[33m)[m fix: the sales node can handle the specs requirements for now and the conversation nodeno longer calls the db connection just for name and phone number
[33md497603[m fix: full name is now committed to db instead of an incomplete name and the redundant code for pronouns is removed
[33m43f6332[m Merge pull request #12 from Immanuel-Khang/testing
[33m2923dfa[m[33m ([m[1;31morigin/testing[m[33m)[m Merge branch 'demo' into testing
[33mc85b4f5[m feat(agent): implement dynamic pronoun adaptation and add performance analysis
[33mbefd98e[m[33m ([m[1;32mtesting[m[33m)[m refactor(agent): switch to neutral pronouns (bạn/mình) across prompts and nodes
[33mcd3a825[m feat(agent): personalize customer pronouns and optimize compose latency
[33mbff0ca2[m fix: remove unnecessary nodes and update compose node to use LLM agent for smoother answer
[33m0476b33[m fix: name and phone correction is now updated to get committed to databse
[33m9c90475[m Merge pull request #10 from Immanuel-Khang/feat/rag-pgvector
[33m9588141[m[33m ([m[1;31morigin/feat/rag-pgvector[m[33m)[m Merge branch 'demo' into feat/rag-pgvector
[33m26975a3[m fix: adapt the current code the new architecture
[33mfdedb9c[m Merge pull request #9 from Immanuel-Khang/testing
[33m97657b7[m Merge pull request #11 from Immanuel-Khang/feat/frontend
[33m9c729a0[m[33m ([m[1;31morigin/feat/frontend[m[33m)[m Merge branch 'demo' into feat/frontend
[33macd6196[m Merge branch 'demo' into feat/rag-pgvector
[33mb9d3e8d[m Merge branch 'demo' into testing
[33m916def0[m feat(agent): add customer name extraction, validation and persistence across turns
[33m8333e9c[m git commit -m "fix: prevent repeating previous vehicle specs in multi-turn conversations"
[33m955d6b9[m feat: add tool-based sales agent
[33m4e7d677[m fix: the previous bot doesnt remember context for the next turn so we fix it
[33m8b9258b[m[33m ([m[1;31morigin/fix/new_schema[m[33m, [m[1;32mfix/new_schema[m[33m)[m feat: change the existing schema
[33m5c833b2[m[33m ([m[1;32mfeat/remember[m[33m)[m feat: testing the transition flow
[33md714b35[m[33m ([m[1;31morigin/feat/remember[m[33m)[m fix: the conversation history is fixed so that the LLM knows what it has said and customers have said
[33ma1c5172[m feat(frontend): improve client and admin interfaces
[33mb7988b2[m feat: remeber context hisotry
[33md43215f[m[33m ([m[1;31morigin/feat/new[m[33m, [m[1;32mfeat/new[m[33m)[m fix: sales flow refactoring, which includes intent extraction before deciding what to do next
[33m5b080db[m feat(agent): enhance intent routing, slot extraction, and booking flow guards
[33m2e34f48[m[33m ([m[1;31morigin/feat/conversation_agent[m[33m, [m[1;32mfeat/conversation_agent[m[33m)[m test: add tests for several tasks requiring human logic, but not yet implemented
[33mcf55467[m docs(rag): document running RAG on Postgres (pgvector)
[33m4f9a28f[m test(rag): add pgvector tests, config flags and eval configs
[33m2755a43[m feat(rag): add PgVectorStore and Postgres full-text lexical branch
[33m7c2b1e7[m feat(db): add rag_chunks table with pgvector and full-text index
[33m95b3279[m fix: changes for smoother conversation
[33m8235816[m docs: simplify README with project overview and docker setup
[33mf93014b[m feat(docker): add docker environment and makefile automation
[33m8e5ac65[m feat(catalog): add commercial truck models and adjust customer schema
[33m55a887c[m chore: update .gitignore
[33mf3f43bf[m feat: add a conversation agent that handles greeting, out of scope question for the system
[33m56a95bd[m[33m ([m[1;32mfeat/a-hybrid-rag[m[33m)[m feat(appointment): make slot-filling flexible with LLM
[33me1caa49[m feat(agent): add conversation agent for greeting/out-of-scope/cancel
[33maca7ab0[m Merge branch 'test_rag_db' into feat/a-hybrid-rag
[33mc572362[m[33m ([m[1;31morigin/feat/database_migration[m[33m, [m[1;32mfeat/database_migration[m[33m)[m feat(db): implement operations schema and alembic migration setup
[33m6ac0b98[m[33m ([m[1;31morigin/feat/a-hybrid-rag[m[33m)[m docs(rag): add RAG guide and update README retriever row
[33mf59ca93[m feat(eval): add retrieval eval (Recall@k, MRR, false handover/answer)
[33m63efd87[m feat(rag): add sample FAQ knowledge
[33m12e13ee[m test(rag): cover normalize, chunking, BM25, RRF, hybrid fallback, cache, reload
[33m1a10d4e[m feat(rag): add hybrid retriever (BM25 + OpenAI embedding + RRF)
[33m2aa7b7f[m[33m ([m[1;31morigin/feat/parallel_agents[m[33m, [m[1;32mfeat/parallel_agents[m[33m)[m fix: continue responding while waiting for human intervention
[33m6e34410[m feat(agent): support multi-intent routing and parallel read-only agents
[33m1a6fc0d[m[33m ([m[1;31morigin/feat/search_update[m[33m, [m[1;32mrefactor/skeleton[m[33m, [m[1;32mfeat/search_update[m[33m)[m fix: resolve the does not return a list of products using search by budget tool
[33m897a90c[m fix: the output to search_by_budget is now displayed to the customer instead of being blocked by the output_guard
[33mf59a89c[m fix: adapt the services for better response and include new search services like search_by_budget and get popular
[33m92c25bf[m Revise README with installation and usage instructions
[33m6a0c4f9[m Merge pull request #7 from Immanuel-Khang/refactor/skeleton
[33mc9f57ee[m[33m ([m[1;31morigin/refactor/skeleton[m[33m)[m feat: continue responding while waiting for human intervention instead of staying idle
[33m66ae446[m refactor: reorganize project structure and workflow
[33m28578bc[m[33m ([m[1;31morigin/feat/appointment[m[33m)[m feat: add QA and appointment scheduling
[33mc53bd46[m[33m ([m[1;32mfeat/add_slot_filling[m[33m)[m fix: Improve the slot filling logic and revise the discount branch -- auto-accept valid requirement while discarding previously invalid ones
[33m780048b[m Revise the general architecture to fit the LangGraph design
[33m2e12f51[m[33m ([m[1;32mrevise/new_arch[m[33m)[m feat: add slot_filling for shor-term memory and better retention
[33m7f10514[m fix: Revise the pending request workflow, the discount is triggered only by the last tool-calling message
[33m8c873da[m Feature: revise the design of the harness core to include an intent classifier
[33ma179b22[m[33m ([m[1;31morigin/main[m[33m, [m[1;31morigin/HEAD[m[33m, [m[1;32mmain[m[33m)[m Merge pull request #3 from Immanuel-Khang/temp_main
[33m67b4638[m[33m ([m[1;31morigin/temp_main[m[33m)[m docs: add detailed analysis for the harness core workflow
[33mbaa4ae6[m Merge pull request #2 from Immanuel-Khang/harness/core
[33ma5071b7[m[33m ([m[1;31morigin/harness/core[m[33m)[m feat: Change from fixed loop to a while loop conversation
[33m449cfdd[m feat: Change from fixed response to a while loop conversation
[33m8e26a39[m feat: add requirements.txt and .env.gitignore for easier setup
[33md48a2ff[m Build the first prototype(24/9/2026) with the simplest harness loops and simulation db
[33m0b10379[m Merge pull request #1 from Immanuel-Khang/architecture/design
[33mc816c04[m The designs for the whole system
[33m277d4d1[m Initial commit
