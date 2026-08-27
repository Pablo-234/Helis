import json

from helis.budget import CycleBudget
from helis.domain import Experiment, ExperimentType, Opportunity
from helis.experiment_designer import ExperimentDesigner
from helis.model_provider import ModelResult
from helis.policy import AutonomyPolicy
from helis.skeptic import VentureSkeptic
from helis.validation import rank_experiments


class QueueProvider:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)

    def complete(self, *, system: str, user: str) -> ModelResult:
        return ModelResult(content=json.dumps(self.responses.pop(0)), prompt_tokens=10, completion_tokens=10)


def test_skeptic_and_designer_share_bounded_budget() -> None:
    opportunity = Opportunity(
        title="Quote delay",
        problem="Customers repeatedly wait hours for manually prepared quotes.",
        customer="service businesses",
        proposed_value="Reduce quote turnaround time.",
    )
    provider = QueueProvider(
        [
            {
                "assumptions": [
                    {
                        "statement": "Customers care enough about response time to switch.",
                        "failure_mode": "Faster quotes do not change buying behavior.",
                        "falsifier": "Prospects show no preference for materially faster responses.",
                        "criticality": 9,
                        "uncertainty": 8,
                    }
                ],
                "contradictions": [],
                "missing_evidence": ["No willingness-to-pay evidence."],
            },
            {
                "experiments": [
                    {
                        "opportunity_id": str(opportunity.id),
                        "title": "Response-time evidence review",
                        "experiment_type": "desk_research",
                        "hypothesis": "Response speed materially affects conversion.",
                        "success_metric": "credible studies or datasets",
                        "success_threshold": "at least 2 independent sources",
                        "targeted_assumptions": [0],
                        "expected_information_gain": 7,
                        "effort_score": 2,
                        "max_cost_cents": 0,
                        "max_duration_hours": 2,
                        "requires_external_contact": False,
                        "requires_publication": False,
                    }
                ]
            },
        ]
    )
    budget = CycleBudget(max_model_calls=2, max_tokens=1000)

    report = VentureSkeptic(provider, budget).review(opportunity)
    experiments = ExperimentDesigner(provider, budget).design(opportunity, report)

    assert report.max_assumption_risk == 7.2
    assert len(experiments) == 1
    assert budget.model_calls == 2


def test_policy_prefers_executable_zero_cost_experiment() -> None:
    opportunity = Opportunity(
        title="Test venture",
        problem="A recurring expensive workflow wastes meaningful employee time.",
        customer="small businesses",
        proposed_value="Reduce the repetitive work.",
    )
    research = Experiment(
        opportunity_id=opportunity.id,
        title="Desk research",
        experiment_type=ExperimentType.DESK_RESEARCH,
        hypothesis="The pain is documented repeatedly.",
        success_metric="independent evidence count",
        success_threshold="3 sources",
        expected_information_gain=6,
        effort_score=2,
    )
    outreach = Experiment(
        opportunity_id=opportunity.id,
        title="Customer interviews",
        experiment_type=ExperimentType.INTERVIEW,
        hypothesis="Customers confirm the pain.",
        success_metric="confirmed interviews",
        success_threshold="5 of 8",
        expected_information_gain=9,
        effort_score=3,
        requires_external_contact=True,
    )

    ranked = rank_experiments([outreach, research], AutonomyPolicy())

    assert ranked[0].experiment.id == research.id
    assert ranked[0].executable
    assert ranked[1].requires_approval
