"""Existing frameworks use the same metered gateway; neither owns an API key."""
import asyncio
import json


async def crewai_json(service, role, system, value, max_tokens):
    loop=asyncio.get_running_loop()

    def execute():
        from crewai import Agent, Crew, Task
        from crewai.llms.base_llm import BaseLLM
        from pydantic import PrivateAttr

        class MeteredLLM(BaseLLM):
            _calls: int = PrivateAttr(default=0)
            def call(self,messages,tools=None,callbacks=None,available_functions=None,**kwargs):
                self._calls+=1
                if self._calls>2: raise RuntimeError("CrewAI specialist call limit reached")
                future=asyncio.run_coroutine_threadsafe(service.json(role,system,
                    {"shared_specification":value,"agent_context":messages},max_tokens),loop)
                try:
                    return "Final Answer: "+json.dumps(future.result(timeout=400))
                except BaseException:
                    future.cancel()
                    raise
            def supports_function_calling(self): return False

        model=MeteredLLM(model="studio/"+role)
        agent=Agent(role=role,goal="Produce the validated shared contract",backstory=system,llm=model,
            allow_delegation=False,max_iter=1,max_retry_limit=0,verbose=False)
        task=Task(description="Produce the requested JSON contract from: "+json.dumps(value),
            expected_output="Only the requested JSON object.",agent=agent)
        result=Crew(agents=[agent],tasks=[task],cache=False,verbose=False).kickoff()
        raw=result.raw
        if raw.strip().startswith("```"): raw=raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)

    return await asyncio.to_thread(execute)


async def autogen_json(service,role,system,value,max_tokens):
    from autogen_agentchat.agents import AssistantAgent
    from autogen_core.models import ChatCompletionClient, CreateResult, RequestUsage

    class MeteredClient(ChatCompletionClient):
        def __init__(self): self.usage=RequestUsage(0,0); self.calls=0
        async def create(self,messages,**kwargs):
            self.calls+=1
            if self.calls>2: raise RuntimeError("AutoGen specialist call limit reached")
            result=await service.json(role,system,{"shared_specification":value,
                "agent_context":[m.model_dump(mode="json") for m in messages]},max_tokens)
            usage=service.job.data["costs"][-1].get("usage",{}) if service.job.data["costs"] else {}
            self.usage=RequestUsage(usage.get("prompt_tokens",0),usage.get("completion_tokens",0))
            return CreateResult(finish_reason="stop",content=json.dumps(result),usage=self.usage,cached=False)
        async def create_stream(self,messages,**kwargs): yield await self.create(messages,**kwargs)
        async def close(self): pass
        def actual_usage(self): return self.usage
        def total_usage(self): return self.usage
        def count_tokens(self,messages,**kwargs): return len(str(messages).encode("utf-8"))
        def remaining_tokens(self,messages,**kwargs): return max(0,120000-self.count_tokens(messages))
        @property
        def model_info(self): return dict(vision=False,function_calling=False,json_output=True,structured_output=False,family="unknown")
        @property
        def capabilities(self): return self.model_info

    client=MeteredClient()
    agent=AssistantAgent(name="UnityQA",model_client=client,system_message=system,
        reflect_on_tool_use=False,model_client_stream=False)
    try:
        result=await agent.run(task=json.dumps(value))
        return json.loads(result.messages[-1].content)
    finally: await client.close()
