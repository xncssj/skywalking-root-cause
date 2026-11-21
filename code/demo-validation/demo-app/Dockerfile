FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -q -B -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -q -B -DskipTests package

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /build/target/demo-app-1.0.0.jar app.jar
COPY agent/skywalking-agent /app/agent
EXPOSE 8080
ENV JAVA_OPTS="-javaagent:/app/agent/skywalking-agent.jar -Xms256m -Xmx256m"
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
