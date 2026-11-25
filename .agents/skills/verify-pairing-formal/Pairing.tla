----------------------------- MODULE Pairing -----------------------------
EXTENDS Naturals, FiniteSets

CONSTANT AtomicConsume
Requests == {1, 2}
VARIABLES pc, snapshot, eligible, accepted, codePresent, matching, live
vars == <<pc, snapshot, eligible, accepted, codePresent, matching, live>>

\* One minted code generation. Input strings collapse to match / mismatch;
\* TTL and timestamp collapse to live / expired. Neither changes in this run.
Init ==
    /\ pc = [r \in Requests |-> "read"]
    /\ snapshot = [r \in Requests |-> FALSE]
    /\ eligible = [r \in Requests |-> FALSE]
    /\ accepted = {}
    /\ codePresent = TRUE
    /\ matching \in [Requests -> BOOLEAN]
    /\ live \in BOOLEAN

\* server._pair_code_matches: reads, checks, clears, and returns while
\* holding _pair_lock. No other modeled request observes an intermediate state.
Redeem(r) ==
    /\ AtomicConsume
    /\ pc[r] = "read"
    /\ accepted' = IF codePresent /\ live /\ matching[r]
                    THEN accepted \cup {r} ELSE accepted
    /\ codePresent' = IF codePresent /\ (~live \/ matching[r])
                       THEN FALSE ELSE codePresent
    /\ pc' = [pc EXCEPT ![r] = "done"]
    /\ UNCHANGED <<snapshot, eligible, matching, live>>

\* Supported broken code variant: the `with _pair_lock` is replaced by
\* `if True`. Both requests can cache the valid code before either clears it.
ReadUnprotected(r) ==
    /\ ~AtomicConsume
    /\ pc[r] = "read"
    /\ snapshot' = [snapshot EXCEPT ![r] = codePresent]
    /\ eligible' = [eligible EXCEPT ![r] = codePresent /\ live /\ matching[r]]
    /\ pc' = [pc EXCEPT ![r] = "finish"]
    /\ UNCHANGED <<accepted, codePresent, matching, live>>

FinishUnprotected(r) ==
    /\ ~AtomicConsume
    /\ pc[r] = "finish"
    /\ accepted' = IF eligible[r] THEN accepted \cup {r} ELSE accepted
    /\ codePresent' = IF snapshot[r] /\ (~live \/ matching[r])
                       THEN FALSE ELSE codePresent
    /\ pc' = [pc EXCEPT ![r] = "done"]
    /\ UNCHANGED <<snapshot, eligible, matching, live>>

Done == /\ \A r \in Requests : pc[r] = "done"
        /\ UNCHANGED vars

Next == (\E r \in Requests : Redeem(r))
     \/ (\E r \in Requests : ReadUnprotected(r))
     \/ (\E r \in Requests : FinishUnprotected(r))
     \/ Done

AtMostOneRedemption == Cardinality(accepted) <= 1
=============================================================================
