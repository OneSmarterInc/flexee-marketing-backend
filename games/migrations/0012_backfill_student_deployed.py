from django.db import migrations


def forwards(apps, schema_editor):
    """Preserve behavior for sims that existed before two-stage deployment: any sim already
    deployed to faculty is treated as already released to students."""
    Game = apps.get_model("games", "Game")
    for game in Game.objects.filter(deployed=True, student_deployed=False):
        game.student_deployed = True
        game.student_deployed_at = game.deployed_at or game.created_at
        game.save(update_fields=["student_deployed", "student_deployed_at"])


def backwards(apps, schema_editor):
    # No-op: the two-stage flag can't be meaningfully reversed.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("games", "0011_game_schedule_game_student_deployed_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
