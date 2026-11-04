import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import session_store as store
from backend.settings import ExperimentSettings
from pydantic import ValidationError
from copy import deepcopy
import sqlite3
from contextlib import closing
import subprocess
import json
import test_experiment_consistency as fixtures


class SettingsTests(unittest.TestCase):
    def test_browser_defaults_match_server_contract(self):
        result=subprocess.check_output(["node","--input-type=module","-e",
            "import {DEFAULT_SETTINGS} from './frontend/settings.js';console.log(JSON.stringify(DEFAULT_SETTINGS));"],text=True)
        self.assertEqual(json.loads(result),ExperimentSettings().model_dump())

    def test_end_experiment_is_idempotent_and_new_settings_do_not_rewrite_history(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(store,"DB_PATH",Path(directory)/"end.db"):
            store.init_database()
            old=store.get_or_create_experiment(800,600,{"sessions":2,"attempts":3,"sensitivity":1.0})
            ended=store.end_experiment(old["experiment_id"])
            self.assertTrue(ended["ended"]);self.assertIsNone(ended["next_condition"])
            self.assertEqual(ended,store.end_experiment(old["experiment_id"]))
            new=store.get_or_create_experiment(800,600,{"sessions":1,"attempts":1,"sensitivity":2.0})
            self.assertNotEqual(new["experiment_id"],old["experiment_id"])
            self.assertEqual(store.get_experiment(old["experiment_id"])["protocol"],old["protocol"])
            self.assertEqual(len(store.history_experiments()),2)

    def test_bounds(self):
        for value in ({"sessions":0}, {"attempts":101}, {"sensitivity":float("nan")}):
            with self.assertRaises(ValidationError):
                ExperimentSettings(**value)

    def test_snapshot_survives_restart_and_new_preferences(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(store,"DB_PATH",Path(directory)/"test.db"):
            store.init_database()
            original=store.get_or_create_experiment(800,600,{"sessions":2,"attempts":5,"sensitivity":1.2})
            store.init_database()
            resumed=store.get_or_create_experiment(800,600,{"sessions":10,"attempts":8,"sensitivity":2.0})
            self.assertEqual(original,resumed)
            self.assertEqual(len(resumed["schedule"]),2)
            self.assertEqual(resumed["protocol"]["target_count"],5)

    def test_all_settings_boundaries_and_strict_types(self):
        cases=[{"sessions":True},{"attempts":1.5},{"time_limit_seconds":-1},{"time_limit_seconds":3601},
               {"countdown_seconds":11},{"crosshair_size":3},{"crosshair_thickness":9},
               {"crosshair_type":"Triangle"},{"crosshair_color":"red"},{"target_color":"#fff"},
               {"background":"white"},{"hit_sound":1},{"miss_feedback":"false"}]
        for value in cases:
            with self.subTest(value=value),self.assertRaises(ValidationError):ExperimentSettings(**value)
        self.assertEqual(ExperimentSettings(sessions=30,attempts=100,sensitivity=5.0,time_limit_seconds=3600,countdown_seconds=10).sessions,30)

    def test_archive_idempotency_analysis_isolation_and_snapshot_guard(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(store,"DB_PATH",Path(directory)/"isolated.db"):
            store.init_database()
            state=store.get_or_create_experiment(800,600,{"sessions":2,"attempts":3,"sensitivity":1.0})
            session=fixtures.make_session(state)
            session["settingsSnapshot"]=deepcopy(state["protocol"]["settings"])
            partial={**deepcopy(session),"status":"incomplete","interruptionReason":"user_ended","attempts":[]}
            store.save_incomplete_session(partial);store.save_incomplete_session(partial)
            self.assertEqual(store.get_experiment(state["experiment_id"])["completed_count"],0)
            self.assertEqual(len(store.history_sessions(state["experiment_id"])),1)
            self.assertIsNone(store.history_session_detail(partial["sessionId"])["statistics"])
            with self.assertRaises(store.ExperimentConflict):store.save_incomplete_session({**partial,"interruptionReason":"escape"})
            session["sessionId"]="completed-after-interruption"
            changed=deepcopy(session);changed["settingsSnapshot"]["attempts"]=5
            with self.assertRaises(store.ExperimentConflict):store.save_session(changed)
            store.save_session(session);store.init_database()
            self.assertEqual(store.history_session_detail(session["sessionId"])["statistics"]["attempt_count"],3)
            self.assertEqual(store.get_experiment(state["experiment_id"])["completed_count"],1)
            self.assertEqual(len(store.get_sessions_by_experiment(state["experiment_id"])),1)
            self.assertEqual(len(store.history_sessions(date="2026-09-06")),2)
            self.assertEqual(store.history_sessions(scene="other"),[])
            self.assertEqual(len(store.history_experiments()),1)
            with closing(sqlite3.connect(store.DB_PATH)) as connection,connection:
                original=connection.execute("SELECT raw_data FROM incomplete_sessions").fetchone()[0]
                connection.execute("UPDATE incomplete_sessions SET raw_data='[]'")
            with self.assertRaises(store.ExperimentConflict):store.history_session_detail(partial["sessionId"])
            with closing(sqlite3.connect(store.DB_PATH)) as connection,connection:
                connection.execute("UPDATE incomplete_sessions SET raw_data=?",(original,))
            with self.assertRaises(sqlite3.OperationalError),patch.object(store,"_connect",side_effect=sqlite3.OperationalError("unavailable")):
                store.save_incomplete_session({**partial,"sessionId":"db-error"})

    def test_empty_experiments_and_old_schema_additive_migration(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(store,"DB_PATH",Path(directory)/"legacy.db"):
            store.init_database()
            state=store.get_or_create_experiment(800,600)
            session=fixtures.make_session(state);store.save_session(session)
            before=store.get_session(session["sessionId"])
            with closing(sqlite3.connect(store.DB_PATH)) as connection,connection:
                connection.execute("DROP TABLE incomplete_sessions")
                connection.execute("DROP TABLE experiment_endings")
            store.init_database()
            self.assertEqual(store.get_session(session["sessionId"]),before)
            self.assertEqual(store.history_experiments()[0]["saved_sessions"],1)
            # A managed experiment with no saved rows must still appear in history.
            with closing(sqlite3.connect(store.DB_PATH)) as connection,connection:
                connection.execute("DELETE FROM sessions")
            self.assertEqual(store.history_experiments()[0]["saved_sessions"],0)

    def test_legacy_association_is_derived_only_for_statistics(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(store,"DB_PATH",Path(directory)/"legacy-raw.db"):
            store.init_database();state=store.get_or_create_experiment(800,600)
            session=fixtures.make_session(state);store.save_session(session)
            session.pop("experimentId")
            with closing(sqlite3.connect(store.DB_PATH)) as connection,connection:
                connection.execute("UPDATE sessions SET raw_data=?",(json.dumps(session),))
            result=store.history_session_detail(session["sessionId"])
            self.assertEqual(result["derived_fields"],{"experimentId":"sessions.experiment_id"})
            self.assertEqual(result["statistics"]["attempt_count"],3)
            self.assertEqual(store.get_session(session["sessionId"]),session)

    def test_new_attempt_ids_reject_repeated_entries_and_old_ids_remain_optional(self):
        from analysis.session_validator import validate_session_integrity
        session=fixtures.make_session({"protocol":{"version":"1","target_radius":25,"target_count":3,"trainer_width":800,"trainer_height":600},
            "experiment_id":"attempt-fixture","completed_count":0,"next_condition":{"sensitivity":1.0,"repeat":1}})
        self.assertEqual(validate_session_integrity(session),[])
        for attempt in session["attempts"]:attempt["attemptId"]="duplicate"
        self.assertTrue(any("unique" in message for message in validate_session_integrity(session)))

    def test_old_three_field_settings_and_missing_snapshot_remain_compatible(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(store,"DB_PATH",Path(directory)/"old-settings.db"):
            store.init_database();state=store.get_or_create_experiment(800,600,{"sessions":2,"attempts":3,"sensitivity":1.0})
            protocol=deepcopy(state["protocol"]);protocol.pop("settings_contract_version")
            protocol["settings"]={"sessions":2,"attempts":3,"sensitivity":1.0}
            with closing(sqlite3.connect(store.DB_PATH)) as connection,connection:
                connection.execute("UPDATE experiments SET protocol=?",(json.dumps(protocol),))
            session=fixtures.make_session(state)
            store.save_session(session)
            self.assertEqual(store.get_experiment(state["experiment_id"])["completed_count"],1)

    def test_partial_status_cannot_be_promoted_through_complete_entry(self):
        from analysis.session_validator import validate_session_integrity
        from test_session_contract import valid_session
        session=valid_session();session["status"]="incomplete"
        self.assertTrue(validate_session_integrity(session))
        with self.assertRaises(ValueError),patch.object(store,"_connect",side_effect=AssertionError("Must reject before SQL")):
            store.save_session(session)
