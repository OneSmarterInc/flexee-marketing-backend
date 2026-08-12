from django.contrib import admin

from . import models

for _m in (
    models.Game, models.Team, models.RoundSubmission, models.RoundResult,
    models.FocusGroupSession, models.PersonaRelationship, models.AdvisorConsultation,
    models.DefenseSubmission, models.GradeCache,
):
    admin.site.register(_m)
